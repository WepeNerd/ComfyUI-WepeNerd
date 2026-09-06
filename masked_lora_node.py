"""Krea2 spatial adapters and the private IMAGE snapshot sink used by the editor."""

import base64
import copy
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


SPATIAL_LAYER = re.compile(r"diffusion_model\.(first|last\.linear|blocks\.\d+\.(attn\.(wq|wk|wv|wo|gate)|mlp\.(up|down|gate)))\.weight$")
PREFIX = "data:image/png;base64,"


def decode_mask(value):
    if not value:
        return torch.zeros(1, 1, 1024, 1024), "empty"
    data = json.loads(value)
    if data.get("v") != 1 or not isinstance(data.get("png"), str) or not data["png"].startswith(PREFIX):
        raise ValueError("Load LoRA Masked: invalid mask data; reopen the mask editor.")
    raw = base64.b64decode(data["png"][len(PREFIX):], validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "PNG" or image.size != (data["width"], data["height"]):
            raise ValueError("Load LoRA Masked: mask geometry does not match its PNG.")
        # The editor stores white coverage in alpha, never the magenta preview.
        raster = image.getchannel("A") if image.mode == "RGBA" else image.convert("L")
        array = np.array(raster, dtype=np.float32) / 255.0
    digest = hashlib.sha256(raw).hexdigest()
    return torch.from_numpy(array)[None, None], digest


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


class RegionContext:
    def __init__(self, mask):
        self.mask = mask
        self.current = ContextVar("wepenerd_masked_lora_layout", default=None)

    def __deepcopy__(self, memo):
        return self

    def __call__(self, executor, x, timesteps, context, attention_mask=None,
                 ref_latents=None, transformer_options=None, **kwargs):
        model = executor.class_obj
        if x.ndim != 4:
            raise ValueError("Load LoRA Masked supports native Krea2 still-image sampling only.")
        patch = model.patch
        h, w = ((x.shape[-2] + patch - 1) // patch, (x.shape[-1] + patch - 1) // patch)
        method = kwargs.get("ref_latents_method", model.default_ref_method)
        refs = 0
        if ref_latents is not None and len(ref_latents) and method is not None:
            if method not in ("index", "index_timestep_zero") or any(r.ndim != 4 for r in ref_latents):
                raise ValueError("Load LoRA Masked: unsupported Krea2 reference token layout.")
            refs = sum(((r.shape[-2] + patch - 1) // patch) * ((r.shape[-1] + patch - 1) // patch) for r in ref_latents)
        if (transformer_options or {}).get("patches", {}).get("post_input"):
            raise ValueError("Load LoRA Masked cannot combine with patches that change input tokens.")
        mask = F.interpolate(self.mask, size=(h, w), mode="bilinear", align_corners=False)
        mask = mask.flatten(2).transpose(1, 2).to(device=x.device, dtype=x.dtype)
        token = self.current.set((context.shape[1], h * w, refs, mask))
        try:
            return executor(x, timesteps, context, attention_mask, ref_latents, transformer_options or {}, **kwargs)
        finally:
            self.current.reset(token)


class RegionalForward:
    def __init__(self, original, adapter, region, strength, image_only=False):
        self.original = original
        self.adapter = adapter
        self.region = region
        self.strength = strength
        self.image_only = image_only

    def __deepcopy__(self, memo):
        return self

    def __call__(self, x, *args, **kwargs):
        layout = self.region.current.get()
        if layout is None:
            raise RuntimeError("Load LoRA Masked: regional layer called outside the Krea2 wrapper.")
        text, count, refs, mask = layout
        start = 0 if self.image_only else text
        if x.ndim != 3 or x.shape[1] != start + count + refs:
            raise ValueError("Load LoRA Masked: token layout changed; cannot safely place the mask.")
        output = self.original(x, *args, **kwargs)
        if self.strength == 0:
            return output
        # Device copies live for this layer call only. The descriptor owns CPU weights.
        adapter = copy.copy(self.adapter)
        adapter.weights = tuple(v.to(device=x.device, dtype=x.dtype) if isinstance(v, torch.Tensor) else v for v in self.adapter.weights)
        if adapter.name == "lokr":
            w1, w2, alpha, _, b1, _, b2, _, _ = adapter.weights
            # ComfyUI's weight path uses the second factor's rank when both are factored;
            # its bypass currently uses the first. Preserve the weight-path scaling.
            if w1 is None and w2 is None and alpha is not None:
                adapter.multiplier = b1.shape[0] / b2.shape[0]
            elif w1 is not None and w2 is not None:
                adapter.weights = (*adapter.weights[:2], None, *adapter.weights[3:])
        target = output[:, start:start + count]
        delta = adapter.h(x[:, start:start + count], target)
        output = output.clone()
        output[:, start:start + count] = torch.addcmul(target, delta, mask.to(target), value=self.strength)
        return output


def validate_linear(module, key):
    import comfy.ops
    from comfy.quant_ops import QuantizedTensor

    # MixedPrecisionOps.Linear inherits Module + CastWeightBiasOp, not nn.Linear.
    native_linear = isinstance(module, (torch.nn.Linear, comfy.ops.CastWeightBiasOp)) and hasattr(module, "in_features") and hasattr(module, "out_features")
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


def validate_adapter(adapter, module, key):
    name = getattr(adapter, "name", "unknown")
    if name not in ("lora", "lokr"):
        raise ValueError(f"Load LoRA Masked: unsupported adapter format {name} at {key}.")
    weights = adapter.weights
    if name == "lora":
        up, down, _, mid, dora, reshape = weights
        if mid is not None or dora is not None or reshape is not None or up.ndim != 2 or down.ndim != 2:
            raise ValueError("Load LoRA Masked supports linear LoRA without DoRA, convolution or reshape variants.")
        shape = (up.shape[0], down.shape[1])
        if up.shape[1] != down.shape[0]:
            raise ValueError(f"Load LoRA Masked: incompatible LoRA rank at {key}.")
    else:
        w1, w2, _, a1, b1, a2, b2, t2, dora = weights
        if t2 is not None or dora is not None or any(v.ndim != 2 for v in (w1, w2, a1, b1, a2, b2) if v is not None):
            raise ValueError("Load LoRA Masked supports linear LoKr without Tucker or DoRA variants.")
        if (w1 is None and (a1 is None or b1 is None)) or (w2 is None and (a2 is None or b2 is None)):
            raise ValueError(f"Load LoRA Masked: incomplete LoKr weights at {key}.")
        s1 = w1.shape if w1 is not None else (a1.shape[0], b1.shape[1])
        s2 = w2.shape if w2 is not None else (a2.shape[0], b2.shape[1])
        shape = (s1[0] * s2[0], s1[1] * s2[1])
    if shape != (module.out_features, module.in_features):
        raise ValueError(f"Load LoRA Masked: adapter shape {shape} does not match {key}.")


class WepeNerdLoadLoraMasked:
    DESCRIPTION = "Krea2 with native floating-point or INT8 ConvRot weights (Load Diffusion Model). Paint where the spatial LoRA contribution applies. Empty mask = no effect. Text/modulation layers are omitted; indirect effects can spread outside the mask."
    CATEGORY = "WepeNerd/Loaders"
    RETURN_TYPES = ("MODEL",)
    FUNCTION = "load"

    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths

        return {"required": {
            "model": ("MODEL",),
            "lora_name": (folder_paths.get_filename_list("loras"),),
            "strength": ("FLOAT", {"default": 1.0, "min": -20.0, "max": 20.0, "step": 0.01}),
            "mask_data": ("STRING", {"default": "", "multiline": False}),
        }, "optional": {"image": ("IMAGE", {"lazy": True})}, "hidden": {"unique_id": "UNIQUE_ID"}}

    def check_lazy_status(self, **kwargs):
        # The editor acquires IMAGE explicitly using the private snapshot sink.
        return []

    def load(self, model, lora_name, strength, mask_data="", image=None, unique_id=""):
        import comfy.lora
        import comfy.lora_convert
        import comfy.model_base
        import comfy.patcher_extension
        import comfy.utils
        import folder_paths

        if not isinstance(model.model, comfy.model_base.Krea2):
            raise ValueError("Load LoRA Masked requires a native Krea2 MODEL.")
        if not math.isfinite(strength):
            raise ValueError("Load LoRA Masked: strength must be finite.")
        mask, mask_hash = decode_mask(mask_data)
        result = model.clone()
        if strength == 0 or not torch.any(mask):
            return (result,)
        if lora_name not in folder_paths.get_filename_list("loras"):
            raise ValueError("Load LoRA Masked: choose a LoRA from the installed list.")
        path = folder_paths.get_full_path_or_raise("loras", lora_name)
        with open(path, "rb") as source:
            lora_hash = hashlib.file_digest(source, "sha256").hexdigest()
        weights = comfy.lora_convert.convert_lora(comfy.utils.load_torch_file(path, safe_load=True))
        patches = comfy.lora.load_lora(weights, comfy.lora.model_lora_keys_unet(model.model, {}))
        region = RegionContext(mask)
        usable = 0
        skipped = 0
        for key, adapter in patches.items():
            if not isinstance(key, str) or not SPATIAL_LAYER.fullmatch(key):
                skipped += 1
                continue
            module_path = key[:-7]
            module = model.get_model_object(module_path)
            validate_linear(module, key)
            validate_adapter(adapter, module, key)
            forward_path = module_path + ".forward"
            result.add_object_patch(forward_path, RegionalForward(model.get_model_object(forward_path), adapter, region, strength, module_path == "diffusion_model.first"))
            usable += 1
        if not usable:
            raise ValueError("Load LoRA Masked: no supported spatial LoRA/LoKr layers matched Krea2.")
        logging.info("Load LoRA Masked: %s spatial layers, %s text/nonspatial layers omitted.", usable, skipped)
        descriptor = MaskedDescriptor(str(unique_id), path, lora_hash, strength, mask_hash, mask.shape[-1], mask.shape[-2])
        previous = model.get_attachment("wepenerd_masked_loras") or ()
        result.set_attachments("wepenerd_masked_loras", previous + (descriptor,))
        # ModelPatcher compares attachment keys when deciding whether clones can share a load.
        identity = hashlib.sha256(repr(descriptor).encode()).hexdigest()
        result.set_attachments("wepenerd_masked_lora_" + identity, descriptor)
        result.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, "wepenerd_masked_lora_" + str(unique_id), region)
        save_asset(Image.fromarray((mask[0, 0].numpy() * 255).astype(np.uint8)))
        return (result,)


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
