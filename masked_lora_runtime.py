"""Bounded CPU file reuse and run-owned adapter/mask residency."""

from collections import OrderedDict
import copy
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
from threading import RLock

import torch


MIB = 1024 * 1024


def env_megabytes(name, default):
    try:
        value = int(os.environ.get(name, default))
        if value < 0:
            raise ValueError
    except ValueError:
        logging.warning("Load LoRA Masked: %s must be a nonnegative integer; using %s MiB.", name, default)
        value = default
    return value * MIB


@dataclass(frozen=True)
class RuntimePolicy:
    cache_bytes: int = 256 * MIB
    headroom_bytes: int = 1024 * MIB
    max_entries: int = 512

    @classmethod
    def from_env(cls):
        return cls(env_megabytes("WEPENERD_MASKED_LORA_CACHE_MB", 256),
                   env_megabytes("WEPENERD_MASKED_LORA_HEADROOM_MB", 1024))


@dataclass(frozen=True)
class AdapterData:
    identity: tuple
    native: object

    @property
    def nbytes(self):
        return sum(v.numel() * v.element_size() for v in self.native.weights if isinstance(v, torch.Tensor))


@dataclass(frozen=True)
class AdapterFile:
    signature: tuple
    digest: str
    adapters: tuple

    @property
    def nbytes(self):
        return sum(adapter.nbytes for _, adapter in self.adapters)


def file_signature(path):
    path = Path(path).resolve()
    stat = path.stat()
    return (os.path.normcase(str(path)), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AdapterFileCache:
    """Only parsed CPU tensors; model mappings/validation never enter this cache."""
    def __init__(self, max_bytes=256 * MIB, max_entries=4):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.resident_bytes = 0
        self.generation = 0
        self.lock = RLock()

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.resident_bytes = 0
            self.generation += 1

    def _remove(self, key):
        self.resident_bytes -= self.entries.pop(key).nbytes

    def load(self, path, parser):
        with self.lock:
            for _ in range(2):
                before = file_signature(path)
                canonical = before[0]
                previous = self.entries.get(canonical)
                if previous is not None:
                    if previous.signature == before:
                        self.entries.move_to_end(canonical)
                        return previous
                    self._remove(canonical)
                digest = hash_file(canonical)
                identical = next((v for v in self.entries.values() if v.digest == digest), None)
                try:
                    adapters = identical.adapters if identical else parser(canonical, digest)
                except Exception:
                    if file_signature(path) != before:
                        continue
                    raise
                if file_signature(path) != before:
                    continue
                entry = AdapterFile(before, digest, adapters)
                if entry.nbytes <= self.max_bytes and self.max_bytes > 0 and self.max_entries > 0:
                    while self.entries and (self.resident_bytes + entry.nbytes > self.max_bytes or len(self.entries) >= self.max_entries):
                        self._remove(next(iter(self.entries)))
                    self.entries[canonical] = entry
                    self.resident_bytes += entry.nbytes
                return entry
            raise ValueError("Load LoRA Masked: adapter file changed while reading; finish saving it and queue again.")


def normalized_adapter(adapter, device, dtype, convert=None):
    """Normalize native LoKr alpha without copying or modifying CPU master weights."""
    result = copy.copy(adapter)
    convert = convert or (lambda value: value.to(device=device, dtype=dtype))
    result.weights = tuple(convert(v) if isinstance(v, torch.Tensor) else v for v in adapter.weights)
    if adapter.name == "lokr":
        w1, w2, alpha, _, b1, _, b2, _, _ = result.weights
        rank = b2.shape[0] if w2 is None else b1.shape[0] if w1 is None else None
        # Verified against ComfyUI's weight path, including unequal factor ranks
        # and direct/direct zero alpha. The backend runs a compatibility probe.
        result.weights = (*result.weights[:2], None, *result.weights[3:])
        result.multiplier = alpha / rank if alpha is not None and rank is not None else 1.0
    return result


class SamplingRuntime:
    def __init__(self, policy=None, free_memory=None):
        self.policy = policy or RuntimePolicy.from_env()
        self.free_memory = free_memory
        self.entries = OrderedDict()
        self.zero_masks = OrderedDict()
        self.truncated_masks = set()
        self.resident_bytes = 0
        self.closed = False
        self.stats = dict(adapter_hits=0, mask_hits=0, conversions=0, conversion_bytes=0,
                          host_to_device_copies=0, host_to_device_bytes=0, mask_projections=0,
                          mask_transfer_bytes=0, evictions=0, streamed=0, peak_cache_bytes=0,
                          base_calls=0, output_copies=0, inplace_outputs=0)

    def close(self):
        self.entries.clear()
        self.zero_masks.clear()
        self.truncated_masks.clear()
        self.resident_bytes = 0
        self.free_memory = None
        self.closed = True

    def _available(self, device):
        if self.free_memory is None:
            import comfy.model_management
            free = comfy.model_management.get_free_memory(device)
        else:
            free = self.free_memory(device)
        return max(0, free - self.policy.headroom_bytes)

    def _evict(self, key=None):
        _, size = self.entries.pop(next(iter(self.entries)) if key is None else key)
        self.resident_bytes -= size
        self.stats["evictions"] += 1

    def _make_room(self, size, device):
        # Keep the admitted working set during cyclic layer visits; overflow streams.
        # Device pressure can evict entries independently of cache admission.
        if device.type != "cpu":
            available = self._available(device)
            while self.entries and size > available:
                self._evict()
                available = self._available(device)
            if size > available:
                raise RuntimeError("Load LoRA Masked: insufficient adapter working memory after reserved headroom. Reduce batch/resolution or adapter size.")

    def _get(self, key, hit_counter):
        if self.closed:
            raise RuntimeError("Load LoRA Masked: sampling runtime is already closed.")
        if key in self.entries:
            self.entries.move_to_end(key)
            self.stats[hit_counter] += 1
            return self.entries[key][0]
        return None

    def _store(self, key, value, size):
        if key[0] == "mask" and 0 < size <= self.policy.cache_bytes and self.policy.max_entries > 0:
            # A new layout must not reproject/transfer its mask at every layer.
            # Prefer displacing adapters, then the least recently used mask.
            while self.entries and (self.resident_bytes + size > self.policy.cache_bytes
                                    or len(self.entries) >= self.policy.max_entries):
                victim = next((k for k in self.entries if k[0] == "adapter"), next(iter(self.entries)))
                self._evict(victim)
        if (self.policy.cache_bytes > 0 and self.resident_bytes + size <= self.policy.cache_bytes
                and len(self.entries) < self.policy.max_entries):
            self.entries[key] = (value, size)
            self.resident_bytes += size
            self.stats["peak_cache_bytes"] = max(self.stats["peak_cache_bytes"], self.resident_bytes)
        else:
            self.stats["streamed"] += 1
        return value

    def adapter(self, data, device, dtype):
        device = torch.device(device)
        key = ("adapter", data.identity, device, dtype)
        cached = self._get(key, "adapter_hits")
        if cached is not None:
            return cached
        size = sum(v.numel() * dtype.itemsize for v in data.native.weights if isinstance(v, torch.Tensor))
        self._make_room(size, device)

        def convert(value):
            converted = value.to(device=device, dtype=dtype)
            if converted is not value:
                count = converted.numel() * converted.element_size()
                self.stats["conversions"] += 1
                self.stats["conversion_bytes"] += count
                if value.device.type == "cpu" and converted.device.type != "cpu":
                    self.stats["host_to_device_copies"] += 1
                    self.stats["host_to_device_bytes"] += count
            return converted

        return self._store(key, normalized_adapter(data.native, device, dtype, convert), size)

    def mask(self, region, layout, device, dtype, project):
        device = torch.device(device)
        g = layout.geometry
        if region.mask.shape[0] > layout.chunk_size:
            warning = (region.descriptor.mask_hash, region.mask.shape[0], layout.chunk_size)
            if warning not in self.truncated_masks:
                logging.warning("Load LoRA Masked: MASK has %s rows but the sampling batch has %s; only the first %s mask rows are used.",
                                region.mask.shape[0], layout.chunk_size, layout.chunk_size)
                self.truncated_masks.add(warning)
        rows = layout.mask_rows(region.mask.shape[0])
        geometry_key = (region.descriptor.mask_hash, tuple(region.mask.shape), g.latent_height,
                        g.latent_width, g.patch, "native-circular", layout.chunk_size, rows)
        key = ("mask", geometry_key, device, dtype)
        cached = self._get(key, "mask_hits")
        if cached is not None:
            return cached
        if geometry_key in self.zero_masks:
            self.zero_masks.move_to_end(geometry_key)
            return None
        master = project(region.mask, g.latent_height, g.latent_width, g.patch)
        self.stats["mask_projections"] += 1
        if not torch.any(master[list(set(rows))]):
            self.zero_masks[geometry_key] = True
            while len(self.zero_masks) > self.policy.max_entries:
                self.zero_masks.popitem(last=False)
            return None
        master = master[list(rows)].flatten(2).transpose(1, 2)
        size = master.numel() * dtype.itemsize
        self._make_room(size, device)
        mask = master.to(device=device, dtype=dtype)
        if device.type != "cpu":
            self.stats["mask_transfer_bytes"] += size
        return self._store(key, mask, size)
