"""Native INT8 ConvRot integration: ComfyUI venv, optionally pass --cuda."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parents[1])]

import comfy.cli_args
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cuda", action="store_true")
parser.add_argument("--checkpoint", type=Path, help="Optional native Krea2 checkpoint; tests its first attention query layer only")
args = parser.parse_args() if __name__ == "__main__" else parser.parse_args([])
comfy.cli_args.args.cpu = not args.cuda

import torch
import comfy.model_base
import comfy.model_patcher
import comfy.ops
import comfy.supported_models
from comfy.quant_ops import QuantizedTensor
from comfy.weight_adapter.lora import LoRAAdapter
from comfy.weight_adapter.lokr import LoKrAdapter
from masked_lora_node import RegionContext, RegionalForward, WepeNerdLoadLoraMasked, validate_linear
from tests.test_masked_lora import mask_json

DEVICE = torch.device("cuda" if args.cuda else "cpu")
DTYPE = torch.bfloat16 if DEVICE.type == "cuda" else torch.float32


def quantized_linear(weight, bias=None, convrot=True):
    q = QuantizedTensor.from_float(weight.to(device=DEVICE, dtype=DTYPE), "TensorWiseINT8Layout",
                                  is_weight=True, per_channel=True, convrot=convrot, convrot_groupsize=64)
    layer = comfy.ops.mixed_precision_ops(compute_dtype=DTYPE).Linear(
        weight.shape[1], weight.shape[0], bias=bias is not None, device="cpu")
    conf = {"format": "int8_tensorwise", "convrot": convrot, "convrot_groupsize": 64}
    state = {"weight": q._qdata.cpu(), "weight_scale": q._params.scale.cpu(),
             "comfy_quant": torch.tensor(list(json.dumps(conf).encode()), dtype=torch.uint8)}
    if bias is not None:
        state["bias"] = bias.cpu()
    layer.load_state_dict(state)
    return layer.to(DEVICE)


def validate_layers():
    layer = quantized_linear(torch.randn(256, 256) * .02, torch.randn(256) * .01)
    validate_linear(layer, "int8")
    # Mixed checkpoints also contain ordinary weights in MixedPrecisionOps.Linear.
    floating = comfy.ops.mixed_precision_ops(compute_dtype=DTYPE).Linear(64, 64, bias=False, device="cpu")
    floating.load_state_dict({"weight": torch.randn(64, 64)})
    validate_linear(floating, "float")
    for unsupported in [torch.nn.Linear(64, 64).to(torch.float64),
                        quantized_linear(torch.randn(64, 64), convrot=False)]:
        try:
            validate_linear(unsupported, "unsupported")
        except ValueError:
            pass
        else:
            raise AssertionError("Unsupported layer was accepted")

    x = torch.randn(2, 67, 256, device=DEVICE, dtype=DTYPE)
    original_x = x.clone()
    packed = layer.weight._qdata.clone()
    variants = [LoRAAdapter(set(), (torch.randn(256, 8) * .1, torch.randn(8, 256) * .1, 12., None, None, None)),
                LoKrAdapter(set(), (torch.randn(16, 16) * .1, torch.randn(16, 16) * .1, None, None, None, None, None, None, None))]
    for adapter in variants:
        region = RegionContext(None)
        native_adapter = type(adapter)(set(), tuple(w.to(device=DEVICE, dtype=DTYPE) if isinstance(w, torch.Tensor) else w for w in adapter.weights))
        for strength, coverage in [(0., 1.), (1., 0.), (1., 1.), (-.7, .5)]:
            mask = torch.full((1, 64, 1), coverage, device=DEVICE, dtype=DTYPE)
            mask[:, :16] = 0
            region.current.set((2, 64, 1, mask))
            # Fail if the node expands the packed base weights to floating point.
            with patch.object(QuantizedTensor, "dequantize", side_effect=AssertionError("Base weight dequantized")):
                baseline = layer(x)
                actual = RegionalForward(layer.forward, adapter, region, strength)(x)
            expected = baseline.clone()
            target = baseline[:, 2:66]
            expected[:, 2:66] = torch.addcmul(target, native_adapter.h(x[:, 2:66], target), mask, value=strength)
            torch.testing.assert_close(actual, expected, rtol=0, atol=0)
            torch.testing.assert_close(actual[:, :18], baseline[:, :18], rtol=0, atol=0)
            torch.testing.assert_close(actual[:, 66:], baseline[:, 66:], rtol=0, atol=0)
        first = RegionalForward(layer.forward, adapter, region, 1.)
        second = RegionalForward(first, adapter, region, -.5)
        a = first(x)
        expected = a.clone()
        expected[:, 2:66] = torch.addcmul(a[:, 2:66], native_adapter.h(x[:, 2:66], a[:, 2:66]), mask, value=-.5)
        torch.testing.assert_close(second(x), expected, rtol=0, atol=0)
    torch.testing.assert_close(x, original_x, rtol=0, atol=0)
    torch.testing.assert_close(layer.weight._qdata, packed, rtol=0, atol=0)
    layer.to("cpu").to(DEVICE)
    validate_linear(layer, "reloaded")
    torch.testing.assert_close(layer(x), baseline, rtol=0, atol=0)
    print(f"PASS ({DEVICE}): serialized INT8 ConvRot, mixed float layers, LoRA/LoKr, masks, text/ref exclusion, chaining, packed weights retained and CPU/device transfer")


def validate_model():
    config = comfy.supported_models.Krea2({"image_model": "krea2", "features": 64, "heads": 4, "kvheads": 2,
        "txtdim": 32, "txtlayers": 2, "txtheads": 2, "txtkvheads": 2, "layers": 1, "multiplier": 1})
    config.set_inference_dtype(DTYPE, None)
    base = comfy.model_base.Krea2(config, device=DEVICE)
    for parameter in base.parameters():
        parameter.data.uniform_(-.02, .02)
    attn = base.diffusion_model.blocks[0].attn
    attn.wq = quantized_linear(attn.wq.weight)
    patcher = comfy.model_patcher.ModelPatcher(base, DEVICE, torch.device("cpu"))
    prefix = "diffusion_model.blocks.0.attn.wq"
    weights = {prefix + ".lora_up.weight": torch.randn(64, 2) * .1,
               prefix + ".lora_down.weight": torch.randn(2, 64) * .1}
    x = torch.randn(1, 16, 4, 4, device=DEVICE, dtype=DTYPE)
    time, context = torch.ones(1, device=DEVICE, dtype=DTYPE), torch.randn(1, 3, 64, device=DEVICE, dtype=DTYPE)
    original = attn.wq.forward
    packed = attn.wq.weight._qdata.clone()
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / "test.pt"
        torch.save(weights, file)
        with patch("folder_paths.get_filename_list", return_value=["test.pt"]), patch("folder_paths.get_full_path_or_raise", return_value=str(file)), patch("folder_paths.get_input_directory", return_value=directory):
            loader = WepeNerdLoadLoraMasked()
            a = loader.load(patcher, "test.pt", 1., mask_json([[255, 0], [255, 0]]), unique_id="1")[0]
            b = loader.load(a, "test.pt", -.5, mask_json([[0, 255], [0, 255]]), unique_id="2")[0]
            empty = loader.load(patcher, "test.pt", 1., "", unique_id="3")[0]
            zero = loader.load(patcher, "test.pt", 0., mask_json([[255]]), unique_id="4")[0]
        assert not empty.object_patches and not zero.object_patches
        for model in [a, b.clone()]:
            model.patch_model(load_weights=False)
            try:
                for refs in [None, [torch.randn(1, 16, 2, 4, device=DEVICE, dtype=DTYPE)]]:
                    result = base.diffusion_model(x, time, context, ref_latents=refs, ref_latents_method="index_timestep_zero", transformer_options={"wrappers": model.wrappers})
                    assert result.shape == x.shape and torch.isfinite(result).all()
            finally:
                model.unpatch_model(unpatch_weights=False)
            assert attn.wq.forward == original
            torch.testing.assert_close(attn.wq.weight._qdata, packed, rtol=0, atol=0)

        global_first = patcher.clone()
        global_adapter = LoRAAdapter(set(), (weights[prefix + ".lora_up.weight"], weights[prefix + ".lora_down.weight"], None, None, None, None))
        global_first.add_patches({prefix + ".weight": global_adapter}, strength_patch=.25)
        with patch("folder_paths.get_filename_list", return_value=["test.pt"]), patch("folder_paths.get_full_path_or_raise", return_value=str(file)), patch("folder_paths.get_input_directory", return_value=directory):
            combined = loader.load(global_first, "test.pt", 1., mask_json([[255]]), unique_id="5")[0]
        combined.add_patches({prefix + ".weight": global_adapter}, strength_patch=.5)
        global_only = global_first.clone()
        global_only.add_patches({prefix + ".weight": global_adapter}, strength_patch=.5)
        probe = torch.randn(1, 7, 64, device=DEVICE, dtype=DTYPE)
        global_only.patch_model(device_to=DEVICE)
        try:
            globally_patched = attn.wq(probe)
        finally:
            global_only.unpatch_model()
        combined.patch_model(device_to=DEVICE)
        try:
            assert isinstance(attn.wq.weight, QuantizedTensor)
            forward = attn.wq.forward
            forward.region.current.set((3, 4, 0, torch.ones(1, 4, 1, device=DEVICE, dtype=DTYPE)))
            actual = attn.wq(probe)
            expected = globally_patched.clone()
            up, down = (w.to(device=DEVICE, dtype=DTYPE) for w in global_adapter.weights[:2])
            expected[:, 3:] += torch.nn.functional.linear(torch.nn.functional.linear(probe[:, 3:], down), up)
            torch.testing.assert_close(actual, expected, rtol=0, atol=0)
            result = base.diffusion_model(x, time, context, transformer_options={"wrappers": combined.wrappers})
            assert torch.isfinite(result).all()
        finally:
            combined.unpatch_model()
        assert attn.wq.forward == original
        torch.testing.assert_close(attn.wq.weight._qdata.cpu(), packed.cpu(), rtol=0, atol=0)
    print(f"PASS ({DEVICE}): Krea2/ModelPatcher load, empty/zero no-op, clones, reference images, global LoRA before/after and unpatch cleanup")


def validate_checkpoint(path):
    from safetensors import safe_open

    prefix = "blocks.0.attn.wq."
    with safe_open(path, framework="pt", device="cpu") as source:
        state = {key: source.get_tensor(prefix + key) for key in ("weight", "weight_scale", "comfy_quant")}
    out_features, in_features = state["weight"].shape
    layer = comfy.ops.mixed_precision_ops(compute_dtype=DTYPE).Linear(in_features, out_features, bias=False, device="cpu")
    layer.load_state_dict(state)
    layer.to(DEVICE)
    validate_linear(layer, prefix)
    x = torch.randn(1, 67, in_features, device=DEVICE, dtype=DTYPE)
    adapter = LoRAAdapter(set(), (torch.randn(out_features, 8) * .01, torch.randn(8, in_features) * .01, None, None, None, None))
    region = RegionContext(None)
    mask = torch.zeros(1, 64, 1, device=DEVICE, dtype=DTYPE)
    mask[:, 32:] = 1
    region.current.set((2, 64, 1, mask))
    with patch.object(QuantizedTensor, "dequantize", side_effect=AssertionError("Base weight dequantized")):
        baseline = layer(x)
        actual = RegionalForward(layer.forward, adapter, region, .8)(x)
    up, down = (w.to(device=DEVICE, dtype=DTYPE) for w in adapter.weights[:2])
    expected = baseline.clone()
    delta = torch.nn.functional.linear(torch.nn.functional.linear(x[:, 2:66], down), up)
    expected[:, 2:66] = torch.addcmul(baseline[:, 2:66], delta, mask, value=.8)
    torch.testing.assert_close(actual, expected, rtol=0, atol=0)
    assert torch.isfinite(actual).all() and not torch.equal(actual, baseline)
    print(f"PASS ({DEVICE}): {path.name}, real {out_features}x{in_features} query layer, group size {layer.weight._params.convrot_groupsize}, masked delta without base dequantization")


if __name__ == "__main__":
    torch.manual_seed(123)
    validate_layers()
    validate_model()
    if args.checkpoint:
        validate_checkpoint(args.checkpoint)
