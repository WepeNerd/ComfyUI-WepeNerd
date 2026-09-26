"""Krea2 spatial adapters and the private IMAGE snapshot sink used by the editor."""

import base64
import binascii
import hashlib
import io
import json
import logging
import math
import re
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from .masked_lora_runtime import (AdapterData, AdapterFileCache, RuntimePolicy, SamplingRuntime,
                                  env_megabytes, file_signature, normalized_adapter)


SPATIAL_LAYER = re.compile(r"diffusion_model\.(first|last\.linear|blocks\.\d+\.(attn\.(wq|wk|wv|wo|gate)|mlp\.(up|down|gate)))\.weight$")
PREFIX = "data:image/png;base64,"
MAX_MASK_SIDE = 16384
MAX_MASK_PIXELS = 64 * 1024 * 1024
MAX_MASK_PAYLOAD = 96 * 1024 * 1024
FILE_CACHE = AdapterFileCache(env_megabytes("WEPENERD_MASKED_LORA_FILE_CACHE_MB", 256))
WRAPPER_KEY = "wepenerd_masked_lora"


def validate_mask_size(height, width, batch=1):
    if any(type(v) is not int or v <= 0 for v in (height, width, batch)):
        raise ValueError("Load LoRA Masked: mask dimensions must be positive integers.")
    if max(height, width) > MAX_MASK_SIDE or batch * height * width > MAX_MASK_PIXELS:
        raise ValueError("Load LoRA Masked: mask exceeds 16384 pixels per side or 64 Mi pixels total.")


def canonical_mask(mask):
    if not isinstance(mask, torch.Tensor) or mask.ndim not in (2, 3):
        raise ValueError("Load LoRA Masked: MASK input must have shape [batch, height, width] or [height, width].")
    validate_mask_size(*mask.shape[-2:], mask.shape[0] if mask.ndim == 3 else 1)
    if mask.is_complex():
        raise ValueError("Load LoRA Masked: mask coverage must be real numbers.")
    mask = mask.detach().to(device="cpu", copy=True)
    if not torch.isfinite(mask).all():
        raise ValueError("Load LoRA Masked: mask contains NaN or Infinity; supply finite coverage values.")
    if torch.any((mask < 0) | (mask > 1)):
        logging.warning("Load LoRA Masked: clamped MASK coverage to [0, 1].")
    mask = mask.clamp(0, 1).to(dtype=torch.float32).reshape(-1, 1, *mask.shape[-2:]).contiguous()
    mask.add_(0.0)  # Canonicalize negative zero as well as dtype and shape.
    digest = hashlib.sha256(str(tuple(mask.shape)).encode())
    digest.update(mask.numpy().tobytes())
    return mask, digest.hexdigest()


def decode_mask(value):
    if value == "":
        return canonical_mask(torch.zeros(1024, 1024))
    if not isinstance(value, str) or len(value) > MAX_MASK_PAYLOAD:
        raise ValueError("Load LoRA Masked: mask payload exceeds 96 MiB or is not text.")
    try:
        data = json.loads(value)
        if not isinstance(data, dict) or type(data.get("v")) is not int or data["v"] != 1:
            raise ValueError("Load LoRA Masked: unsupported mask schema version; reopen the editor.")
        validate_mask_size(data.get("height"), data.get("width"))
        if "empty" in data and type(data["empty"]) is not bool:
            raise ValueError("Load LoRA Masked: invalid empty mask marker.")
        if data.get("empty") and "png" not in data:
            return canonical_mask(torch.zeros(data["height"], data["width"]))
        if not isinstance(data.get("png"), str) or not data["png"].startswith(PREFIX):
            raise ValueError("Load LoRA Masked: invalid mask PNG; reopen the editor.")
        raw = base64.b64decode(data["png"][len(PREFIX):], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or image.size != (data["width"], data["height"]):
                raise ValueError("Load LoRA Masked: mask geometry does not match its PNG.")
            if "A" not in image.getbands():
                raise ValueError("Load LoRA Masked: mask PNG must contain alpha coverage.")
            array = np.array(image.getchannel("A"), dtype=np.float32) / 255.0
        if data.get("empty") and np.any(array):
            raise ValueError("Load LoRA Masked: empty marker disagrees with PNG coverage.")
        return canonical_mask(torch.from_numpy(array))
    except (json.JSONDecodeError, binascii.Error, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Load LoRA Masked: invalid mask data; reopen the editor.") from error


def resize_coverage(mask, size):
    """Area/adaptive averaging on shrinking axes, then bilinear on expanding axes."""
    reduced = tuple(min(a, b) for a, b in zip(mask.shape[-2:], size))
    if tuple(mask.shape[-2:]) != reduced:
        mask = F.interpolate(mask, size=reduced, mode="area")
    if reduced != tuple(size):
        mask = F.interpolate(mask, size=size, mode="bilinear", align_corners=False)
    return mask


def project_mask(mask, latent_height, latent_width, patch):
    from comfy.ldm.common_dit import pad_to_patch_size

    if min(latent_height, latent_width, patch) <= 0:
        raise ValueError("Load LoRA Masked: invalid latent/patch geometry.")
    grid = (math.ceil(latent_height / patch), math.ceil(latent_width / patch))
    if latent_height % patch == 0 and latent_width % patch == 0:
        return resize_coverage(mask, grid)
    # Native Krea2 wraps right/bottom padding circularly. Do not stretch into it.
    latent = resize_coverage(mask, (latent_height, latent_width))
    latent = pad_to_patch_size(latent, (patch, patch))
    return F.interpolate(latent, size=grid, mode="area")


def save_asset(image):
    import folder_paths

    output = io.BytesIO()
    image.save(output, format="PNG")
    raw = output.getvalue()
    filename = hashlib.sha256(raw).hexdigest() + ".png"
    directory = Path(folder_paths.get_input_directory()) / "wepenerd_masked_lora"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    if not path.exists():
        path.write_bytes(raw)
    return {"filename": filename, "subfolder": directory.name, "type": "input"}


@dataclass(frozen=True)
class MaskedDescriptor:
    node_id: str
    lora_path: str
    lora_hash: str
    strength: float
    mask_hash: str
    width: int
    height: int
    start_percent: float = 0.0
    end_percent: float = 1.0
    apply_to: str = "Both"


def validate_schedule(start, end, apply_to):
    if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in (start, end)):
        raise ValueError("Load LoRA Masked: start_percent and end_percent must be finite values in [0, 1].")
    if start > end:
        raise ValueError("Load LoRA Masked: start_percent must not exceed end_percent; equal values disable application.")
    if apply_to not in ("Both", "Positive only"):
        raise ValueError("Load LoRA Masked: apply_to must be Both or Positive only.")


def schedule_rows(descriptor, layout, sigmas, model_sampling, positive_verified):
    if descriptor.apply_to == "Positive only" and (not positive_verified or any(v not in (0, 1) for v in layout.chunk_labels)):
        raise ValueError("Load LoRA Masked: Positive only requires native KSampler, CFGGuider or BasicGuider with verified positive/negative chunk metadata. Use Both for other guiders.")
    if descriptor.start_percent == descriptor.end_percent:
        return (False,) * len(layout.sample_indices)
    active = (True,) * len(layout.sample_indices)
    if (descriptor.start_percent, descriptor.end_percent) != (0.0, 1.0):
        if sigmas is None or len(sigmas) != len(active):
            raise ValueError("Load LoRA Masked: sampling range requires raw sigma for every activation row through native APPLY_MODEL.")
        # Reconvert each forward from the live object: flow shifts/object patches can change it.
        high = float(model_sampling.percent_to_sigma(descriptor.start_percent))
        low = float(model_sampling.percent_to_sigma(descriptor.end_percent))
        if not math.isfinite(high) or not math.isfinite(low) or low > high:
            raise ValueError("Load LoRA Masked: model sampling must provide finite, decreasing percent_to_sigma endpoints.")
        # Compare in Python double precision after one small sigma readback per forward.
        lower = low - 1e-7 * max(1.0, abs(low))
        upper = high + 1e-7 * max(1.0, abs(high))
        active = tuple(lower <= sigma <= upper for sigma in sigmas)
        logging.debug("Load LoRA Masked %s: percent [%g, %g], sigma [%g, %g], apply_to=%s, chunks=%s, native_positive=%s",
                      descriptor.node_id, descriptor.start_percent, descriptor.end_percent, low, high,
                      descriptor.apply_to, layout.chunk_labels, positive_verified)
    if descriptor.apply_to == "Positive only":
        active = tuple(on and layout.chunk_labels[i // layout.chunk_size] == 0 for i, on in enumerate(active))
    return active


@dataclass(frozen=True)
class RegionSpec:
    descriptor: MaskedDescriptor
    mask: torch.Tensor


@dataclass(frozen=True)
class Contribution:
    adapter: AdapterData
    region: RegionSpec


@dataclass(frozen=True)
class ModelGeometry:
    latent_height: int
    latent_width: int
    patch: int
    text_tokens: int
    image_tokens: int
    reference_tokens: int


@dataclass(frozen=True)
class CallLayout:
    chunk_labels: tuple
    chunk_size: int
    sample_indices: tuple
    geometry: ModelGeometry

    def mask_rows(self, source_batch):
        return tuple(i % source_batch for i in self.sample_indices)


def call_layout(batch, options, geometry, sampling):
    labels = options.get("cond_or_uncond")
    if not isinstance(labels, (list, tuple)) or not labels or any(type(v) is not int or v < 0 for v in labels):
        raise ValueError("Load LoRA Masked: missing/invalid conditioning chunk metadata; use native sampling.")
    if batch % len(labels):
        raise ValueError("Load LoRA Masked: activation batch is not divisible by conditioning chunks.")
    size = batch // len(labels)
    if sampling is None:
        raise ValueError("Load LoRA Masked: unverified sampling layout; use the native conditioning batch path.")
    logical_batch, spatial_shape, branches = sampling
    if size != logical_batch or (geometry.latent_height, geometry.latent_width) != spatial_shape or any(v >= branches for v in labels):
        raise ValueError("Load LoRA Masked: sampling batch/geometry changed; tiles or reordered/subset samples require explicit coordinates and indices.")
    if "uuids" in options and len(options["uuids"]) != len(labels):
        raise ValueError("Load LoRA Masked: inconsistent conditioning chunk metadata.")
    rows = tuple(range(size)) * len(labels)
    return CallLayout(tuple(labels), size, rows, geometry)


class Krea2Context:
    def __init__(self, policy=None, descriptors=()):
        self.policy = policy or RuntimePolicy.from_env()
        self.descriptors = tuple(d for d in descriptors if (d.start_percent, d.end_percent, d.apply_to) != (0., 1., "Both"))
        self.needs_sigma = any(d.start_percent != d.end_percent and (d.start_percent, d.end_percent) != (0., 1.) for d in descriptors)
        self.positive_only = any(d.apply_to == "Positive only" for d in descriptors)
        self.current = ContextVar("wepenerd_masked_lora_layout", default=None)
        self.sampling = ContextVar("wepenerd_masked_lora_sampling", default=None)
        self.runtime = ContextVar("wepenerd_masked_lora_runtime", default=None)
        self.raw_sigma = ContextVar("wepenerd_masked_lora_sigma", default=None)
        self.positive_verified = ContextVar("wepenerd_masked_lora_positive", default=False)
        self.gates = ContextVar("wepenerd_masked_lora_gates", default=None)

    def __deepcopy__(self, memo):
        return self

    def outer_sample(self, executor, *args, **kwargs):
        guider = getattr(executor, "class_obj", None)
        patcher = getattr(guider, "model_patcher", None)
        verified = False
        if self.positive_only:
            from comfy.samplers import CFGGuider
            from comfy_extras.nodes_custom_sampler import Guider_Basic
            from comfy.patcher_extension import WrappersMP, get_all_wrappers

            verified = type(guider) in (CFGGuider, Guider_Basic)
            if not verified or guider.model_options.get("sampler_calc_cond_batch_function") is not None:
                raise ValueError("Load LoRA Masked: Positive only requires native KSampler, CFGGuider or BasicGuider. Custom guiders/conditioning evaluators have unverified branch semantics; use Both.")
            wrappers = get_all_wrappers(WrappersMP.PREDICT_NOISE, guider.model_options, is_model_options=True)
            wrappers += [w for w in get_all_wrappers(WrappersMP.CALC_COND_BATCH, guider.model_options, is_model_options=True)
                         if w != self.conditioning_batch]
            if wrappers or "predict_noise" in guider.__dict__:
                raise ValueError("Load LoRA Masked: Positive only cannot verify custom guider/conditioning wrappers; remove them or use Both.")
            logging.debug("Load LoRA Masked: native guider %s, 0=positive, 1=negative", type(guider).__name__)
        # The patcher includes memory reclaimable by ComfyUI's dynamic offloader.
        # Capture its query, but allocate nothing before prepare_sampling runs.
        runtime = SamplingRuntime(self.policy, getattr(patcher, "get_free_memory", None))
        token = self.runtime.set(runtime)
        positive_token = self.positive_verified.set(verified)
        try:
            return executor(*args, **kwargs)
        finally:
            runtime.close()
            self.runtime.reset(token)
            self.positive_verified.reset(positive_token)
            logging.debug("Load LoRA Masked runtime: %s", runtime.stats)

    def apply_model(self, executor, x, sigma, *args, **kwargs):
        if not self.needs_sigma:
            return executor(x, sigma, *args, **kwargs)
        if sigma.ndim != 1 or sigma.shape[0] != x.shape[0]:
            raise ValueError("Load LoRA Masked: raw sigma batch does not match the native activation batch.")
        values = tuple(sigma.detach().double().cpu().tolist())
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Load LoRA Masked: raw sigma must be finite.")
        token = self.raw_sigma.set((values, executor.class_obj.model_sampling))
        try:
            return executor(x, sigma, *args, **kwargs)
        finally:
            self.raw_sigma.reset(token)

    def conditioning_batch(self, executor, model, conds, x, timestep, model_options):
        if self.positive_only and (not self.positive_verified.get() or len(conds) != 2
                                   or model_options.get("sampler_calc_cond_batch_function") is not None):
            raise ValueError("Load LoRA Masked: Positive only needs verified native positive/negative conditioning lists; use native CFGGuider or Both.")
        if any(model_options.get(k) is not None for k in ("context_handler", "model_function_wrapper", "multigpu_clones")):
            raise ValueError("Load LoRA Masked: tiled/context/custom model wrappers and multi-GPU sampling are not validated; use native still-image sampling.")
        for branch in conds:
            for cond in branch or ():
                if cond.get("area") is not None:
                    raise ValueError("Load LoRA Masked: cropped regional conditioning requires global crop coordinates and is not supported.")
        token = self.sampling.set((x.shape[0], tuple(x.shape[-2:]), len(conds)))
        try:
            return executor(model, conds, x, timestep, model_options)
        finally:
            self.sampling.reset(token)

    def __call__(self, executor, x, timesteps, context, attention_mask=None,
                 ref_latents=None, transformer_options=None, **kwargs):
        model = executor.class_obj
        # Native Krea2 also receives still images as [B, C, 1, H, W].
        if not (x.ndim == 4 or (x.ndim == 5 and x.shape[2] == 1)):
            raise ValueError("Load LoRA Masked supports native Krea2 still-image sampling only.")
        patch = model.patch
        h, w = ((x.shape[-2] + patch - 1) // patch, (x.shape[-1] + patch - 1) // patch)
        method = kwargs.get("ref_latents_method", model.default_ref_method)
        refs = 0
        if ref_latents is not None and len(ref_latents) and method is not None:
            if method not in ("index", "index_timestep_zero") or any(
                not (r.ndim == 4 or (r.ndim == 5 and r.shape[2] == 1)) for r in ref_latents
            ):
                raise ValueError("Load LoRA Masked: unsupported Krea2 reference token layout.")
            refs = sum(((r.shape[-2] + patch - 1) // patch) * ((r.shape[-1] + patch - 1) // patch) for r in ref_latents)
        options = transformer_options or {}
        if options.get("patches", {}).get("post_input"):
            raise ValueError("Load LoRA Masked cannot combine with patches that change input tokens.")
        if context.ndim != 3 or context.shape[0] != x.shape[0]:
            raise ValueError("Load LoRA Masked: unsupported Krea2 text batch layout.")
        geometry = ModelGeometry(*x.shape[-2:], patch, context.shape[1], h * w, refs)
        layout = call_layout(x.shape[0], options, geometry, self.sampling.get())
        sigmas, model_sampling = self.raw_sigma.get() or (None, None)
        gates = {}
        for descriptor in self.descriptors:
            rows = schedule_rows(descriptor, layout, sigmas, model_sampling, self.positive_verified.get())
            gates[descriptor] = (True if all(rows) else False if not any(rows)
                                 else torch.tensor([i for i, active in enumerate(rows) if active], device=x.device, dtype=torch.long))
        token = self.current.set(layout)
        gate_token = self.gates.set(gates)
        try:
            return executor(x, timesteps, context, attention_mask, ref_latents, transformer_options or {}, **kwargs)
        finally:
            self.current.reset(token)
            self.gates.reset(gate_token)


class LayerDispatcher:
    def __init__(self, original, contributions, context, image_only=False, private_output=False):
        self.original = original
        self.contributions = tuple(contributions)
        self.context = context
        self.image_only = image_only
        self.private_output = private_output

    def __deepcopy__(self, memo):
        return self

    def __call__(self, x, *args, **kwargs):
        layout = self.context.current.get()
        runtime = self.context.runtime.get()
        if layout is None or runtime is None:
            raise RuntimeError("Load LoRA Masked: use native sampling with OUTER_SAMPLE and Krea2 layout wrappers; this direct layer call has no sampling lifetime.")
        geometry = layout.geometry
        count = geometry.image_tokens
        start = 0 if self.image_only else geometry.text_tokens
        if x.ndim != 3 or x.shape[0] != len(layout.sample_indices) or x.shape[1] != start + count + geometry.reference_tokens:
            raise ValueError("Load LoRA Masked: token layout changed; cannot safely place the mask.")
        output = self.original(x, *args, **kwargs)
        runtime.stats["base_calls"] += 1
        gates = self.context.gates.get()
        prepared = False
        for contribution in self.contributions:
            descriptor = contribution.region.descriptor
            strength = descriptor.strength
            if descriptor in self.context.descriptors and gates is None:
                raise RuntimeError("Load LoRA Masked: scheduled adapters require the native sigma/layout wrappers.")
            gate = gates.get(descriptor, True) if gates is not None else True
            if strength == 0 or gate is False:
                continue
            mask = runtime.mask(contribution.region, layout, output.device, output.dtype, project_mask)
            if mask is None:
                continue
            if not prepared:
                # Native Linear may return a view of its own fresh allocation.
                # Ownership comes from the verified forward, not output._base.
                can_write = (self.private_output and not torch.is_grad_enabled() and not output.requires_grad
                             and type(output) is torch.Tensor
                             and output.untyped_storage().data_ptr() != x.untyped_storage().data_ptr())
                if can_write:
                    runtime.stats["inplace_outputs"] += 1
                else:
                    output = output.clone()
                    runtime.stats["output_copies"] += 1
                prepared = True
            adapter = runtime.adapter(contribution.adapter, x.device, x.dtype)
            target = output[:, start:start + count]
            source = x[:, start:start + count]
            if gate is True:
                delta = adapter.h(source, target).to(device=output.device, dtype=output.dtype)
                target.addcmul_(delta, mask, value=strength)
            else:
                active = target.index_select(0, gate)
                delta = adapter.h(source.index_select(0, gate), active).to(device=output.device, dtype=output.dtype)
                active.addcmul_(delta, mask.index_select(0, gate), value=strength)
                target.index_copy_(0, gate, active)
                del active
            del adapter, delta, mask
        return output


def native_private_output(module, original):
    # Arbitrary object patches, subclasses and instance overrides take the copy path.
    return (type(module).__module__ in ("torch.nn.modules.linear", "comfy.ops")
            and getattr(original, "__self__", None) is module
            and getattr(original, "__func__", None) is type(module).forward
            and "_forward" not in module.__dict__)


def validate_linear(module, key):
    import comfy.ops
    from comfy.quant_ops import QuantizedTensor

    # MixedPrecisionOps.Linear inherits Module + CastWeightBiasOp, not nn.Linear.
    native_linear = (
        isinstance(module, (torch.nn.Linear, comfy.ops.CastWeightBiasOp))
        and type(module).__module__ in ("torch.nn.modules.linear", "comfy.ops")
        and hasattr(module, "in_features") and hasattr(module, "out_features")
        and getattr(module, "quant_format", None) in (None, "int8_tensorwise")
    )
    weight = getattr(module, "weight", None)
    floating = type(weight) in (torch.Tensor, torch.nn.Parameter) and weight.dtype in (torch.float16, torch.bfloat16, torch.float32)
    convrot = (
        isinstance(weight, QuantizedTensor)
        and getattr(module, "quant_format", None) == "int8_tensorwise"
        and getattr(module, "layout_type", None) == "TensorWiseINT8Layout"
        and weight._layout_cls == "TensorWiseINT8Layout"
        and weight._params.convrot
    )
    if not native_linear or not (floating or convrot):
        raise ValueError(f"Load LoRA Masked: {key} requires native floating-point or INT8 ConvRot linear layers. Use Load Diffusion Model; custom INT8/GGUF loaders and FP8/NVFP4 are not supported.")


def validate_adapter(adapter, module, key, check_values=True):
    name = getattr(adapter, "name", "unknown")
    if name not in ("lora", "lokr"):
        raise ValueError(f"Load LoRA Masked: unsupported adapter format {name} at {key}.")
    weights = adapter.weights
    expected = 6 if name == "lora" else 9
    if len(weights) != expected:
        raise ValueError("Load LoRA Masked: unsupported ComfyUI adapter API; update the node and rerun its numerical tests.")
    alpha = weights[2]
    if alpha is not None and (not isinstance(alpha, (int, float)) or not math.isfinite(alpha)):
        raise ValueError(f"Load LoRA Masked: invalid adapter alpha at {key}.")
    if check_values:
        for weight in weights:
            if isinstance(weight, torch.Tensor) and (not weight.numel() or not torch.isfinite(weight).all()):
                raise ValueError(f"Load LoRA Masked: empty or nonfinite adapter weights at {key}.")
    if name == "lora":
        up, down, _, mid, dora, reshape = weights
        if not isinstance(up, torch.Tensor) or not isinstance(down, torch.Tensor):
            raise ValueError(f"Load LoRA Masked: incomplete LoRA weights at {key}.")
        if mid is not None or dora is not None or reshape is not None or up.ndim != 2 or down.ndim != 2:
            raise ValueError("Load LoRA Masked supports linear LoRA without DoRA, convolution or reshape variants.")
        shape = (up.shape[0], down.shape[1])
        if up.shape[1] != down.shape[0]:
            raise ValueError(f"Load LoRA Masked: incompatible LoRA rank at {key}.")
    else:
        w1, w2, _, a1, b1, a2, b2, t2, dora = weights
        if any(not isinstance(v, torch.Tensor) for v in (w1, w2, a1, b1, a2, b2) if v is not None):
            raise ValueError(f"Load LoRA Masked: malformed LoKr weights at {key}.")
        if t2 is not None or dora is not None or any(v.ndim != 2 for v in (w1, w2, a1, b1, a2, b2) if v is not None):
            raise ValueError("Load LoRA Masked supports linear LoKr without Tucker or DoRA variants.")
        if (w1 is None and (a1 is None or b1 is None)) or (w2 is None and (a2 is None or b2 is None)):
            raise ValueError(f"Load LoRA Masked: incomplete LoKr weights at {key}.")
        for direct, a, b in ((w1, a1, b1), (w2, a2, b2)):
            if direct is not None and (a is not None or b is not None):
                raise ValueError(f"Load LoRA Masked: ambiguous direct/factored LoKr weights at {key}.")
            if direct is None and a.shape[1] != b.shape[0]:
                raise ValueError(f"Load LoRA Masked: incompatible LoKr rank at {key}.")
        s1 = w1.shape if w1 is not None else (a1.shape[0], b1.shape[1])
        s2 = w2.shape if w2 is not None else (a2.shape[0], b2.shape[1])
        shape = (s1[0] * s2[0], s1[1] * s2[1])
    if module is not None and shape != (module.out_features, module.in_features):
        raise ValueError(f"Load LoRA Masked: adapter shape {shape} does not match {key}.")


ADAPTER_TENSOR = re.compile(r"(?:[._]lora_(?:up|down|A|B)(?:\.default)?(?:\.weight)?|_lora\.(?:up|down)\.weight|\.lora\.(?:up|down)\.weight|\.lora_linear_layer\.(?:up|down)\.weight|\.lokr_w[12](?:_[ab])?)$")
UNSUPPORTED_TENSORS = (
    (re.compile(r"\.(?:dora_scale|lora_magnitude_vector)(?:\..*)?$"), "DoRA"),
    (re.compile(r"\.hada_"), "LoHa"),
    (re.compile(r"\.(?:lora_mid|lokr_t2)(?:\.|$)"), "convolution/Tucker"),
    (re.compile(r"\.reshape_weight$"), "reshape/shape-changing adapters"),
    (re.compile(r"\.(?:oft_blocks|rescale|[ab][12]\.weight|diff|diff_b|set_weight|w_norm|b_norm)$"), "non-LoRA/LoKr adapters"),
)


def adapter_tensor_keys(weights):
    keys = set()
    for key, value in weights.items():
        # Training state/metadata is not an adapter just because it is unfamiliar.
        if not isinstance(key, str) or key.startswith(("optimizer.", "optimizer_states.", "metadata.")):
            continue
        for pattern, name in UNSUPPORTED_TENSORS:
            if pattern.search(key):
                raise ValueError(f"Load LoRA Masked does not support {name} ({key}). Use standard LoRA or supported linear LoKr. No adapter was applied.")
        if isinstance(value, torch.Tensor) and ADAPTER_TENSOR.search(key):
            if value.ndim != 2:
                raise ValueError(f"Load LoRA Masked: convolution/nonlinear adapter tensor at {key}. No adapter was applied.")
            keys.add(key)
    return keys


def validate_lokr_api():
    """Fail before patching if upstream changes its weight/bypass scaling contract."""
    from comfy.weight_adapter.lokr import LoKrAdapter

    x = torch.tensor([[[.2, -.3, .4, .1]]])
    for first, second, alpha in ((False, False, 0.), (True, False, 5.), (False, True, 5.), (True, True, 5.)):
        weights = (None if first else torch.ones(2, 2), None if second else torch.ones(2, 2), alpha,
                   torch.ones(2, 2) if first else None, torch.ones(2, 2) if first else None,
                   torch.ones(2, 3) if second else None, torch.ones(3, 2) if second else None, None, None)
        adapter = LoKrAdapter(set(), weights)
        weight = adapter.calculate_weight(torch.zeros(4, 4), "masked_lora_probe", 1., 1., None, lambda v: v)
        delta = normalized_adapter(adapter, "cpu", torch.float32).h(x, torch.zeros(1, 1, 4))
        if weight is None or not torch.allclose(delta, F.linear(x, weight), atol=1e-5, rtol=1e-4):
            raise ValueError("Load LoRA Masked: unsupported ComfyUI LoKr scaling API. Update the node and rerun its numerical tests. No adapter was applied.")


def parse_adapters(weights, digest):
    import comfy.lora

    tensor_keys = adapter_tensor_keys(weights)
    formats = {}
    for tensor_key in tensor_keys:
        prefix = tensor_key[:ADAPTER_TENSOR.search(tensor_key).start()]
        kind = "lokr" if ".lokr_w" in tensor_key else "lora"
        if prefix in formats and formats[prefix] != kind:
            raise ValueError(f"Load LoRA Masked: mixed LoRA/LoKr tensors at {prefix}. No adapter was applied.")
        formats[prefix] = kind
    try:
        patches = comfy.lora.load_lora(weights, {prefix: prefix for prefix in sorted(formats)}, log_missing=False)
    except (KeyError, IndexError, TypeError, ValueError, RuntimeError) as error:
        raise ValueError("Load LoRA Masked: incomplete or malformed adapter tensors. No adapter was applied.") from error
    loaded = set()
    for prefix, adapter in patches.items():
        validate_adapter(adapter, None, prefix)
        loaded.update(adapter.loaded_keys)
    unmapped = tensor_keys - loaded
    if unmapped:
        raise ValueError(f"Load LoRA Masked: incomplete/unparsed adapter tensors ({', '.join(sorted(unmapped)[:3])}). No adapter was applied.")
    if any(adapter.name == "lokr" for adapter in patches.values()):
        validate_lokr_api()
    return tuple((prefix, AdapterData((digest, prefix, adapter.name), adapter)) for prefix, adapter in patches.items())


def read_adapter_file(path, digest):
    import comfy.lora_convert
    import comfy.utils

    weights = comfy.utils.load_torch_file(path, safe_load=True)
    adapter_tensor_keys(weights)
    return parse_adapters(comfy.lora_convert.convert_lora(weights), digest)


def preflight_adapters(adapters, key_map, model):
    selected = []
    omitted = 0
    targets = set()
    for prefix, data in adapters:
        if prefix not in key_map:
            raise ValueError(f"Load LoRA Masked: unmapped adapter tensors ({prefix}). No adapter was applied.")
        key = key_map[prefix]
        if not isinstance(key, str):
            raise ValueError("Load LoRA Masked: offset/shape-changing adapter mappings are unsupported. No adapter was applied.")
        if key in targets:
            raise ValueError(f"Load LoRA Masked: duplicate adapter mappings at {key}. No adapter was applied.")
        targets.add(key)
        if not SPATIAL_LAYER.fullmatch(key):
            omitted += 1
            continue
        module = model.get_model_object(key[:-7])
        validate_linear(module, key)
        validate_adapter(data.native, module, key, check_values=False)
        selected.append((key, data))
    if not selected:
        raise ValueError("Load LoRA Masked: no supported spatial LoRA/LoKr layers matched Krea2.")
    return selected, omitted


class WepeNerdLoadLoraMasked:
    DESCRIPTION = "Applies linear LoRA/LoKr through a spatial mask to native floating-point or INT8 ConvRot Krea2 (Load Diffusion Model). Gray applies partial strength; black adds nothing. Text/nonspatial layers are omitted. Attention can spread changes outside the mask. LoHa, DoRA, GGUF, custom quantized loaders, FP8/NVFP4 and video are unsupported."
    CATEGORY = "WepeNerd/Loaders"
    RETURN_TYPES = ("MODEL", "MASK")
    FUNCTION = "load"

    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths

        return {"required": {
            "model": ("MODEL",),
            "lora_name": (folder_paths.get_filename_list("loras"),),
            "strength": ("FLOAT", {"default": 1.0, "min": -20.0, "max": 20.0, "step": 0.01}),
            "mask_data": ("STRING", {"default": "", "multiline": False}),
        }, "optional": {
            "image": ("IMAGE", {"lazy": True}),
            "mask": ("MASK", {"tooltip": "Overrides the painting. Finite coverage is clamped to [0, 1]; NaN/Infinity is rejected. Returned at full resolution, including at zero strength."}),
            "start_percent": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01, "advanced": True,
                                       "tooltip": "Range follows the model's denoising progression; a partial-denoise run may use only part of it."}),
            "end_percent": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01, "advanced": True,
                                     "tooltip": "Inclusive end of the model denoising range. Equal start/end disables this adapter. Full 0–1 stays active for all sigmas."}),
            "apply_to": (["Both", "Positive only"], {"default": "Both", "advanced": True,
                        "tooltip": "Positive only requires native KSampler/CFGGuider/BasicGuider and changes direct adapter injection, including its interaction with CFG."}),
        }, "hidden": {"unique_id": "UNIQUE_ID"}}

    def check_lazy_status(self, **kwargs):
        # The editor acquires IMAGE explicitly using the private snapshot sink.
        return []

    @classmethod
    def clear_file_cache(cls):
        """Explicit reload for callers preserving file metadata; restart also clears it."""
        FILE_CACHE.clear()

    @classmethod
    def IS_CHANGED(cls, lora_name, **kwargs):
        import folder_paths

        if lora_name not in folder_paths.get_filename_list("loras"):
            return ("missing", lora_name, FILE_CACHE.generation)
        try:
            return (file_signature(folder_paths.get_full_path_or_raise("loras", lora_name)), FILE_CACHE.generation)
        except FileNotFoundError:
            return ("missing", lora_name, FILE_CACHE.generation)

    def load(self, model, lora_name, strength, mask_data="", image=None, unique_id="", mask=None,
             start_percent=0.0, end_percent=1.0, apply_to="Both"):
        import comfy.lora
        import comfy.model_base
        import comfy.patcher_extension
        import comfy.utils
        import folder_paths

        if not hasattr(comfy.model_base, "Krea2"):
            raise RuntimeError("Load LoRA Masked needs ComfyUI with native Krea2 support; tested with ComfyUI 0.37.0. Update ComfyUI and restart.")
        if not isinstance(model.model, comfy.model_base.Krea2):
            raise ValueError("Load LoRA Masked requires a native Krea2 MODEL.")
        if not math.isfinite(strength):
            raise ValueError("Load LoRA Masked: strength must be finite.")
        validate_schedule(start_percent, end_percent, apply_to)
        if mask is None:
            mask, mask_hash = decode_mask(mask_data)
        else:
            mask, mask_hash = canonical_mask(mask)
        result = model.clone()
        if start_percent == end_percent:
            logging.info("Load LoRA Masked: equal start/end percentages disable this adapter.")
        if strength == 0 or start_percent == end_percent or not torch.any(mask):
            return (result, mask.squeeze(1).clone())
        if lora_name not in folder_paths.get_filename_list("loras"):
            raise ValueError("Load LoRA Masked: choose a LoRA from the installed list.")
        path = folder_paths.get_full_path_or_raise("loras", lora_name)
        file = FILE_CACHE.load(path, read_adapter_file)
        patches, skipped = preflight_adapters(file.adapters, comfy.lora.model_lora_keys_unet(model.model, {}), model)
        descriptor = MaskedDescriptor(str(unique_id), path, file.digest, strength, mask_hash, mask.shape[-1], mask.shape[-2],
                                      start_percent, end_percent, apply_to)
        region = RegionSpec(descriptor, mask)
        previous = model.get_attachment("wepenerd_masked_loras") or ()
        context = Krea2Context(descriptors=previous + (descriptor,))
        # Rebind all inherited dispatchers to this branch's single layout/runtime.
        dispatchers = {key: value for key, value in model.object_patches.items() if isinstance(value, LayerDispatcher)}
        for key, data in patches:
            module_path = key[:-7]
            forward_path = module_path + ".forward"
            original = model.get_model_object(forward_path)
            contribution = Contribution(data, region)
            if isinstance(original, LayerDispatcher):
                dispatchers[forward_path] = LayerDispatcher(original.original, original.contributions + (contribution,),
                                                           context, original.image_only, original.private_output)
            else:
                module = model.get_model_object(module_path)
                dispatchers[forward_path] = LayerDispatcher(original, (contribution,), context, module_path == "diffusion_model.first",
                                                           native_private_output(module, original))
        for key, dispatcher in dispatchers.items():
            result.add_object_patch(key, LayerDispatcher(dispatcher.original, dispatcher.contributions, context,
                                                        dispatcher.image_only, dispatcher.private_output))
        logging.info("Load LoRA Masked: %s spatial layers, %s supported text/nonspatial layers omitted.", len(patches), skipped)
        result.set_attachments("wepenerd_masked_loras", previous + (descriptor,))
        # ModelPatcher compares attachment keys when deciding whether clones can share a load.
        identity = hashlib.sha256(repr(previous + (descriptor,)).encode()).hexdigest()
        for key in list(result.attachments):
            if key.startswith("wepenerd_masked_lora_branch_"):
                result.remove_attachments(key)
        result.set_attachments("wepenerd_masked_lora_branch_" + identity, descriptor)
        for kind, wrapper in ((comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, context),
                              (comfy.patcher_extension.WrappersMP.APPLY_MODEL, context.apply_model),
                              (comfy.patcher_extension.WrappersMP.CALC_COND_BATCH, context.conditioning_batch),
                              (comfy.patcher_extension.WrappersMP.OUTER_SAMPLE, context.outer_sample)):
            result.remove_wrappers_with_key(kind, WRAPPER_KEY)
            result.add_wrapper_with_key(kind, WRAPPER_KEY, wrapper)
        return (result, mask.squeeze(1).clone())


class WN_MaskedLoraSnapshot:
    """Editor-only sink: a submitted prompt contains only IMAGE ancestors and this sink."""
    CATEGORY = "_internal"
    RETURN_TYPES = ()
    FUNCTION = "snapshot"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}

    def snapshot(self, image):
        array = (image[0].detach().cpu().clamp(0, 1).numpy() * 255).astype(np.uint8)
        preview = Image.fromarray(array)
        asset = save_asset(preview)
        return {"ui": {"images": [asset], "width": [preview.width], "height": [preview.height]}, "result": ()}
