"""Opt-in CUDA layer microbenchmark; no pretrained checkpoint or image generation.

Run with the ComfyUI Python environment. Results include synchronous wall time,
actual conversion counts, logical destination bytes (not measured PCIe traffic),
and CUDA allocator peaks. See docs/masked-lora-phase2.md for scope and results.
"""

import argparse
import gc
import json
from pathlib import Path
import statistics
import tempfile
import time

import torch

import test_masked_lora as fixtures
from wepenerd_lora_test import masked_lora_runtime as rt

ml = fixtures.ml


def raw_weights(kind, width, seed):
    torch.manual_seed(seed)
    if kind == "lora32":
        return fixtures.lora_weights(out=width, inp=width, rank=32, alpha=32)
    return {"layer.lokr_w1": torch.randn(2, 2) * .1,
            "layer.lokr_w2": torch.randn(width // 2, width // 2) * .01}


def timed(function):
    torch.cuda.synchronize()
    start = time.perf_counter()
    output = function()
    torch.cuda.synchronize()
    return output, (time.perf_counter() - start) * 1000


def file_loading(kind, width, directory):
    path = directory / (kind + ".pt")
    torch.save(raw_weights(kind, width, 41), path)
    cold, hits = [], []
    for _ in range(3):
        cache = rt.AdapterFileCache()
        start = time.perf_counter()
        entry = cache.load(path, ml.read_adapter_file)
        cold.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        assert cache.load(path, ml.read_adapter_file) is entry
        hits.append((time.perf_counter() - start) * 1000)
        cache.clear()
    return dict(kind=kind, file_bytes=path.stat().st_size, parsed_bytes=entry.nbytes,
                hash_parse_ms=statistics.median(cold), stat_hit_ms=statistics.median(hits))


class Phase1Forward:
    """Phase 1's per-region transfer/clone algorithm, consolidated only for timing.

    This gives the same original-once/add-in-order behavior as its nested forwards.
    Masks are prepared before timing, as they were by the diffusion wrapper; this
    deliberately excludes Phase 1's repeated CPU projection/transfer overhead.
    """
    def __init__(self, layer, contributions, layout, device, dtype):
        self.layer = layer
        self.contributions = contributions
        self.masks = [ml.project_mask(c.region.mask, layout.geometry.latent_height,
                                     layout.geometry.latent_width, layout.geometry.patch)
                      .flatten(2).transpose(1, 2).to(device=device, dtype=dtype)
                      for c in contributions]
        self.start = layout.geometry.text_tokens
        self.count = layout.geometry.image_tokens
        self.stats = dict(host_to_device_copies=0, host_to_device_bytes=0,
                          output_copies=0, base_calls=0)

    def convert(self, tensor, device, dtype):
        value = tensor.to(device=device, dtype=dtype)
        if value is not tensor and tensor.device.type == "cpu" and value.device.type == "cuda":
            self.stats["host_to_device_copies"] += 1
            self.stats["host_to_device_bytes"] += value.numel() * value.element_size()
        return value

    def __call__(self, x):
        output = self.layer.forward(x)
        self.stats["base_calls"] += 1
        for contribution, mask in zip(self.contributions, self.masks):
            adapter = rt.normalized_adapter(contribution.adapter.native, x.device, x.dtype,
                                            lambda value: self.convert(value, x.device, x.dtype))
            target = output[:, self.start:self.start + self.count]
            delta = adapter.h(x[:, self.start:self.start + self.count], target)
            output = output.clone()
            self.stats["output_copies"] += 1
            output[:, self.start:self.start + self.count] = torch.addcmul(
                target, delta, mask.to(target), value=contribution.region.descriptor.strength)
            del target, delta, adapter
        return output


def measure(mode, layer, x, contributions, layout, iterations):
    # Allocator reset is between independent benchmark cases, never per layer/step.
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    baseline_allocated = torch.cuda.memory_allocated()
    context = ml.Krea2Context(rt.RuntimePolicy(0 if mode == "stream" else 256 * rt.MIB, 1024 * rt.MIB))
    context.current.set(layout)
    if mode == "phase1":
        forward = Phase1Forward(layer, contributions, layout, x.device, x.dtype)
    else:
        forward = ml.LayerDispatcher(layer.forward, contributions, context, private_output=True)
    torch.cuda.reset_peak_memory_stats()
    captured = {}

    def sample():
        runtime = context.runtime.get()
        output, first_ms = timed(lambda: forward(x))
        del output
        # Warm CUDA kernels and any explicit cache misses before measured warm calls.
        for _ in range(2):
            output = forward(x)
            del output
        times = []
        for _ in range(iterations):
            output, elapsed = timed(lambda: forward(x))
            times.append(elapsed)
            del output
        peak_allocated = torch.cuda.max_memory_allocated()
        peak_reserved = torch.cuda.max_memory_reserved()
        # Correctness readback is outside both timing and peak measurement.
        output = forward(x).float().cpu()
        captured.update(mode=mode, first_ms=first_ms, warm_median_ms=statistics.median(times),
                        warm_min_ms=min(times), warm_max_ms=max(times),
                        baseline_allocated=baseline_allocated, peak_allocated=peak_allocated,
                        extra_peak_allocated=peak_allocated - baseline_allocated,
                        peak_reserved=peak_reserved, calls=iterations + 4,
                        stats=dict(forward.stats if mode == "phase1" else runtime.stats))
        return output

    output = context.outer_sample(sample)
    assert context.runtime.get() is None
    del forward, context
    gc.collect()
    torch.cuda.synchronize()
    captured["allocated_after_cleanup"] = torch.cuda.memory_allocated()
    assert captured["allocated_after_cleanup"] == baseline_allocated
    return output, captured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--width", type=int, default=6144)
    args = parser.parse_args()
    if args.width % 2 or args.iterations < 1:
        parser.error("width must be even and iterations must be positive")
    if not torch.cuda.is_available():
        parser.error("a CUDA GPU is required")
    torch.set_num_threads(4)
    torch.set_grad_enabled(False)
    device, dtype = torch.device("cuda:0"), torch.bfloat16
    torch.manual_seed(42)
    layer = torch.nn.Linear(args.width, args.width, bias=False, device=device, dtype=dtype)
    x = torch.randn(1, 256 + 4096 + 1024, args.width, device=device, dtype=dtype) * .1
    geometry = ml.ModelGeometry(128, 128, 2, 256, 4096, 1024)
    layout = ml.CallLayout((0,), 1, (0,), geometry)
    # Initialize CUDA libraries before any recorded case.
    for _ in range(3):
        layer(x)
    results = dict(device=torch.cuda.get_device_name(), torch=torch.__version__, cuda=torch.version.cuda,
                   dtype=str(dtype), width=args.width, image_resolution=[1024, 1024],
                   latent_geometry=[128, 128], tokens=dict(text=256, target=4096, reference=1024),
                   batch=1, seed=42, iterations=args.iterations, cpu_threads=torch.get_num_threads(),
                   loading=[], cases=[])
    with tempfile.TemporaryDirectory(prefix="masked-lora-benchmark-") as directory:
        for kind in ("lora32", "lokr"):
            results["loading"].append(file_loading(kind, args.width, Path(directory)))
            adapters = [ml.parse_adapters(raw_weights(kind, args.width, 41 + i), (kind, i))[0][1] for i in range(3)]
            for regions in (1, 3):
                for coverage in ("sparse", "full"):
                    mask = torch.ones(1024, 1024)
                    if coverage == "sparse":
                        mask.zero_()
                        mask[384:640, 384:640] = 1.  # Exactly 1/16 of target tokens.
                    contributions = [ml.Contribution(adapters[i], fixtures.region_spec(mask, (.7, -.4, .2)[i]))
                                     for i in range(regions)]
                    reference = None
                    for mode in ("phase1", "resident", "stream"):
                        output, result = measure(mode, layer, x, contributions, layout, args.iterations)
                        if reference is None:
                            reference = output
                        else:
                            torch.testing.assert_close(output, reference, atol=2e-3, rtol=2e-2)
                        result.update(kind=kind, regions=regions, coverage=coverage,
                                      max_abs_difference=(output - reference).abs().max().item())
                        results["cases"].append(result)
                        print(f"{kind} {regions} {coverage} {mode}: {result['warm_median_ms']:.3f} ms, "
                              f"{result['stats']['host_to_device_copies']} copies, "
                              f"{result['extra_peak_allocated'] / rt.MIB:.1f} MiB extra peak", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
