"""Run with the ComfyUI venv; validates native Krea2/ModelPatcher integration on CPU."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parents[1])]

import comfy.cli_args
comfy.cli_args.args.cpu = True

import torch
import comfy.model_base
import comfy.model_patcher
import comfy.patcher_extension
import comfy.supported_models
from comfy.weight_adapter.lora import LoRAAdapter
from comfy.weight_adapter.lokr import LoKrAdapter
from masked_lora_node import WepeNerdLoadLoraMasked, RegionContext, RegionalForward
from tests.test_masked_lora import mask_json


def validate_math():
    torch.manual_seed(123)
    variants = [
        LoRAAdapter(set(), (torch.randn(6, 2), torch.randn(2, 6), 3., None, None, None)),
        LoKrAdapter(set(), (torch.randn(2, 2), torch.randn(3, 3), 2., None, None, None, None, None, None)),
        LoKrAdapter(set(), (torch.randn(2, 2), torch.randn(3, 3), 0., None, None, None, None, None, None)),
        LoKrAdapter(set(), (torch.randn(2, 2), None, 3., None, None, torch.randn(3, 2), torch.randn(2, 3), None, None)),
        LoKrAdapter(set(), (None, None, 3., torch.randn(2, 1), torch.randn(1, 2), torch.randn(3, 2), torch.randn(2, 3), None, None)),
    ]
    for adapter in variants:
        x = torch.randn(2, 4, 6)
        delta = adapter.calculate_weight(torch.zeros(6, 6), "test", 1., 1., None, lambda x: x)
        region = RegionContext(None)
        region.current.set((0, 4, 0, torch.ones(1, 4, 1)))
        actual = RegionalForward(torch.zeros_like, adapter, region, 1.)(x)
        torch.testing.assert_close(actual, torch.nn.functional.linear(x, delta), rtol=1e-5, atol=1e-5)
    print("PASS: native LoRA alpha/rank and full/factored LoKr bypass match weight deltas")


def validate_native():
    config = comfy.supported_models.Krea2({"image_model": "krea2", "features": 64, "heads": 4, "kvheads": 2,
        "txtdim": 32, "txtlayers": 2, "txtheads": 2, "txtkvheads": 2, "layers": 1, "multiplier": 1})
    config.set_inference_dtype(torch.float32, None)
    base = comfy.model_base.Krea2(config, device=torch.device("cpu"))
    for parameter in base.parameters():
        parameter.data.uniform_(-.02, .02)
    patcher = comfy.model_patcher.ModelPatcher(base, torch.device("cpu"), torch.device("cpu"))
    x, time, context = torch.randn(1, 16, 4, 4), torch.ones(1), torch.randn(1, 3, 64)
    prefix = "diffusion_model.blocks.0.attn.wq"
    weights = {prefix + ".lora_up.weight": torch.randn(64, 2) * .01,
               prefix + ".lora_down.weight": torch.randn(2, 64) * .01, prefix + ".alpha": torch.tensor(3.)}
    loader = WepeNerdLoadLoraMasked()
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / "test.pt"; torch.save(weights, file)
        with patch("folder_paths.get_filename_list", return_value=["test.pt"]), patch("folder_paths.get_full_path_or_raise", return_value=str(file)), patch("folder_paths.get_input_directory", return_value=directory):
            a = loader.load(patcher, "test.pt", 1., mask_json([[255, 0], [255, 0]]), unique_id="1")[0]
            b = loader.load(a, "test.pt", -.5, mask_json([[0, 255], [0, 255]]), unique_id="2")[0]
            changed = loader.load(patcher, "test.pt", 1., mask_json([[0, 255], [0, 255]]), unique_id="1")[0]
            empty = loader.load(patcher, "test.pt", 1., "", unique_id="1")[0]
        assert len(b.get_attachment("wepenerd_masked_loras")) == 2
        assert not a.clone_has_same_weights(changed)
        assert empty.object_patches == patcher.object_patches
        original = base.diffusion_model.blocks[0].attn.wq.forward
        for model in [a, b.clone(), changed]:
            model.patch_model(load_weights=False)
            try:
                options = {"wrappers": model.wrappers}
                result = base.diffusion_model(x, time, context, transformer_options=options)
                assert result.shape == x.shape and torch.isfinite(result).all()
                result = base.diffusion_model(x, time, context, ref_latents=[torch.randn(1, 16, 2, 4)], ref_latents_method="index_timestep_zero", transformer_options=options)
                assert result.shape == x.shape and torch.isfinite(result).all()
            finally:
                model.unpatch_model()
            assert base.diffusion_model.blocks[0].attn.wq.forward == original
        global_first = patcher.clone()
        global_first.add_patches({prefix + ".weight": ("diff", (torch.ones(64, 64) * .001,))})
        with patch("folder_paths.get_filename_list", return_value=["test.pt"]), patch("folder_paths.get_full_path_or_raise", return_value=str(file)), patch("folder_paths.get_input_directory", return_value=directory):
            combined = loader.load(global_first, "test.pt", 1., mask_json([[255]]), unique_id="3")[0]
        assert combined.patches == global_first.patches
        combined.add_patches({prefix + ".weight": ("diff", (torch.ones(64, 64) * .002,))})
        assert len(combined.patches[prefix + ".weight"]) == 2
        module = base.diffusion_model.blocks[0].attn.wq
        before = module.weight.detach().clone()
        combined.patch_model()
        try:
            torch.testing.assert_close(module.weight, before + .003)
            result = base.diffusion_model(x, time, context, transformer_options={"wrappers": combined.wrappers})
            assert torch.isfinite(result).all()
        finally:
            combined.unpatch_model()
        torch.testing.assert_close(module.weight, before)
    print("PASS: native Krea2 forward, reference tokens, clones, independent same-file regions, global patch composition and unpatch cleanup")


if __name__ == "__main__":
    validate_math()
    validate_native()
