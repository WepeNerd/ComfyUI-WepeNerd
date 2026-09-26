"""CPU regression/integration tests using the installed ComfyUI adapter APIs."""

import base64
from dataclasses import FrozenInstanceError
import io
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT.parents[1] / "comfy").is_dir():
    sys.path.insert(0, str(ROOT.parents[1]))
import comfy.cli_args
comfy.cli_args.args.cpu = True
import comfy.lora
import comfy.model_base
import comfy.model_patcher
import comfy.patcher_extension as pe
import comfy.samplers
import comfy.supported_models
import comfy.conds
import folder_paths

package = types.ModuleType("wepenerd_lora_test")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
from wepenerd_lora_test import masked_lora_node as ml


def document(alpha, **fields):
    rgba = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    rgba[..., :3] = (216, 77, 157)
    rgba[..., 3] = alpha
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    return json.dumps(dict(v=1, width=alpha.shape[1], height=alpha.shape[0],
                          png=ml.PREFIX + base64.b64encode(buffer.getvalue()).decode(), **fields))


def lora_weights(prefix="layer", out=6, inp=12, rank=2, alpha=None):
    weights = {prefix + ".lora_up.weight": torch.randn(out, rank) * 0.1,
               prefix + ".lora_down.weight": torch.randn(rank, inp) * 0.1}
    if alpha is not None:
        weights[prefix + ".alpha"] = torch.tensor(float(alpha))
    return weights


def lokr_weights(factor1, factor2, alpha):
    weights = {}
    # Nonsquare 6 x 12 layer; deliberately unequal ranks 2 and 3.
    for i, (factored, out, inp, rank) in enumerate(((factor1, 2, 3, 2), (factor2, 3, 4, 3)), 1):
        prefix = f"layer.lokr_w{i}"
        if factored:
            weights[prefix + "_a"] = torch.randn(out, rank) * 0.1
            weights[prefix + "_b"] = torch.randn(rank, inp) * 0.1
        else:
            weights[prefix] = torch.randn(out, inp) * 0.1
    if alpha is not None:
        weights["layer.alpha"] = torch.tensor(float(alpha))
    return weights


def parse(weights):
    return comfy.lora.load_lora(weights, {"layer": "layer.weight"}, log_missing=False)["layer.weight"]


def reference_weight(module, adapters):
    return comfy.lora.calculate_weight([(s, a, 1.0, None, None) for a, s in adapters],
                                       module.weight.detach().clone(), "layer.weight")


def region_spec(mask, strength=1., node_id="test"):
    if mask.ndim == 4:
        mask = mask.squeeze(1)
    master, digest = ml.canonical_mask(mask)
    descriptor = ml.MaskedDescriptor(node_id, "fixture", "fixture", strength, digest, master.shape[-1], master.shape[-2])
    return ml.RegionSpec(descriptor, master)


def contribution(adapter, mask, strength=1., identity=None):
    return ml.Contribution(ml.AdapterData(identity or (id(adapter),), adapter), region_spec(mask, strength))


def run_region(region, callback, batch=1, labels=(0,), shape=(4, 6), patch_size=2, refs=None, rank5=False, options=None):
    x = torch.zeros(batch * len(labels), 2, *shape)
    if rank5:
        x = x.unsqueeze(2)
    options = dict(options or {}, cond_or_uncond=list(labels))
    model = SimpleNamespace(patch=patch_size, default_ref_method="index")
    executor = pe.WrapperExecutor.new_class_executor(callback, model, [region])
    return region.outer_sample(lambda: region.conditioning_batch(
        lambda *args: executor.execute(x, torch.ones(x.shape[0]), torch.zeros(x.shape[0], 2, 4),
                                      None, refs, options),
        None, [None] * (max(labels) + 1), torch.zeros(batch, 2, *shape), torch.ones(batch), {}))


class MaskTests(unittest.TestCase):
    def test_empty_and_legacy_geometry(self):
        for value, shape in (("", (1, 1, 1024, 1024)),
                             (json.dumps(dict(v=1, width=1536, height=1024, empty=True)), (1, 1, 1024, 1536)),
                             (document(np.zeros((7, 13), np.uint8), empty=True), (1, 1, 7, 13))):
            mask, _ = ml.decode_mask(value)
            self.assertEqual(tuple(mask.shape), shape)
            self.assertFalse(mask.any())

    def test_alpha_and_canonical_hash(self):
        values = np.array([[0, 64, 128, 255]], dtype=np.uint8)
        painted, digest = ml.decode_mask(document(values))
        external, external_digest = ml.canonical_mask(torch.tensor(values.astype(np.float32) / 255))
        torch.testing.assert_close(painted, external)
        self.assertEqual(digest, external_digest)
        self.assertNotEqual(digest, ml.canonical_mask(external.reshape(2, 2))[1])

    def test_external_validation_ownership_and_clamping(self):
        original = torch.tensor([[[-2., 0., 0.5, 2.]]], requires_grad=True)
        before = original.detach().clone()
        with self.assertLogs(level="WARNING") as logs:
            canonical, digest = ml.canonical_mask(original)
        self.assertIn("clamped", logs.output[0])
        self.assertEqual(canonical.tolist(), [[[[0., 0., 0.5, 1.]]]])
        self.assertFalse(canonical.requires_grad)
        self.assertEqual(digest, ml.canonical_mask(original.detach().clamp(0, 1))[1])
        canonical.zero_()
        torch.testing.assert_close(original.detach(), before)
        for bad in (torch.zeros(0, 3), torch.zeros(1, 1, 2, 3), torch.zeros(0, 2, 3),
                    torch.tensor([[float("nan")]]), torch.tensor([[float("inf")]]),
                    torch.tensor([[-float("inf")]]), torch.ones(2, 2, dtype=torch.complex64)):
            with self.subTest(shape=bad.shape), self.assertRaises(ValueError):
                ml.canonical_mask(bad)

    def test_malformed_documents(self):
        valid = json.loads(document(np.zeros((2, 3), np.uint8)))
        for changes in ({"v": 2}, {"v": True}, {"width": 0}, {"height": -1}, {"width": 3.0},
                        {"width": 4}, {"width": 20000}, {"width": 16384, "height": 16384},
                        {"empty": "true"}, {"png": ml.PREFIX + "not base64!"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ml.decode_mask(json.dumps(valid | changes))
        for value in ("[1]", "{broken", document(np.ones((2, 3), np.uint8) * 255, empty=True)):
            with self.assertRaises(ValueError):
                ml.decode_mask(value)
        with patch.object(ml, "MAX_MASK_PAYLOAD", 10), self.assertRaisesRegex(ValueError, "payload"):
            ml.decode_mask(json.dumps(valid))


class ProjectionTests(unittest.TestCase):
    def test_stripe_counterexample_and_neighbors_portrait(self):
        for offset, portrait in itertools.product((-2, -1, 0, 1, 2), (False, True)):
            pixels = torch.zeros(1, 1, 64, 1024)
            pixels[..., 489 + offset:503 + offset] = 1
            expected = torch.zeros(1, 1, 4, 64)
            expected[..., 30] = (7 - offset) / 16
            expected[..., 31] = (7 + offset) / 16
            if offset == 0:
                self.assertFalse(F.interpolate(pixels, (4, 64), mode="bilinear", align_corners=False).any())
            if portrait:
                pixels, expected = pixels.transpose(-1, -2), expected.transpose(-1, -2)
            latent = (128, 8) if portrait else (8, 128)
            torch.testing.assert_close(ml.project_mask(pixels, *latent, 2), expected, atol=0, rtol=0)

    def test_small_circle_and_soft_ramp_exact_block_averages(self):
        y, x = torch.meshgrid(torch.arange(64), torch.arange(96), indexing="ij")
        for pixels in (((x - 45) ** 2 + (y - 31) ** 2 < 9).float(), x.float() / 95):
            expected = torch.tensor([[pixels[h:h + 8, w:w + 8].mean() for w in range(0, 96, 8)] for h in range(0, 64, 8)])
            actual = ml.project_mask(pixels[None, None], 16, 24, 2)
            torch.testing.assert_close(actual[0, 0], expected)

    def test_mixed_axes_and_noninteger_adaptive_average(self):
        values = torch.arange(35).reshape(1, 1, 5, 7).float() / 34
        expected = torch.tensor([[values[0, 0, 0:3, 0:3].mean(), values[0, 0, 0:3, 2:5].mean(), values[0, 0, 0:3, 4:7].mean()],
                                 [values[0, 0, 2:5, 0:3].mean(), values[0, 0, 2:5, 2:5].mean(), values[0, 0, 2:5, 4:7].mean()]])
        torch.testing.assert_close(ml.resize_coverage(values, (2, 3))[0, 0], expected)
        reduced = values.reshape(1, 1, 5, 7)
        expected_mixed = F.interpolate(F.adaptive_avg_pool2d(reduced, (2, 7)), (2, 11), mode="bilinear", align_corners=False)
        torch.testing.assert_close(ml.resize_coverage(values, (2, 11)), expected_mixed)
        for size in ((2, 11), (9, 3), (2, 3), (9, 11)):
            torch.testing.assert_close(ml.resize_coverage(torch.full_like(values, .37), size), torch.full((1, 1, *size), .37))

    def test_circular_padding_last_row_column_and_nondefault_patch(self):
        pixels = torch.arange(15).reshape(1, 1, 3, 5).float() / 14
        padded = pixels[0, 0][[0, 1, 2, 0]][:, [0, 1, 2, 3, 4, 0]]
        expected = torch.tensor([[padded[h:h + 2, w:w + 2].mean() for w in range(0, 6, 2)] for h in range(0, 4, 2)])
        torch.testing.assert_close(ml.project_mask(pixels, 3, 5, 2)[0, 0], expected)
        torch.testing.assert_close(ml.project_mask(torch.full((1, 1, 17, 31), .4), 5, 7, 3), torch.full((1, 1, 2, 3), .4))


class BatchTests(unittest.TestCase):
    def test_chunk_alignment_matrix(self):
        for batch, sources, labels in itertools.product((1, 2, 3), (1, 2, 3, 4), ((0, 1), (1, 0), (0, 0, 0), (0,), (1,))):
            region = ml.Krea2Context()
            spec = region_spec(torch.arange(sources).float().reshape(sources, 1, 1) / 4)
            def inspect(*args):
                layout = region.current.get()
                mask = region.runtime.get().mask(spec, layout, "cpu", torch.float32, ml.project_mask)
                expected = tuple(i % sources for i in range(batch)) * len(labels)
                self.assertEqual(layout.mask_rows(sources), expected)
                self.assertEqual(layout.chunk_labels, labels)
                self.assertEqual(layout.chunk_size, batch)
                if any(expected):
                    self.assertEqual(mask[:, 0, 0].tolist(), [i / 4 for i in expected])
                else:
                    self.assertIsNone(mask)
                with self.assertRaises(FrozenInstanceError):
                    layout.chunk_size = 99
            run_region(region, inspect, batch, labels)
            self.assertIsNone(region.current.get())
            self.assertIsNone(region.sampling.get())

    def test_counterexample(self):
        geometry = ml.ModelGeometry(4, 6, 2, 2, 6, 0)
        layout = ml.call_layout(6, {"cond_or_uncond": [0, 1]}, geometry, (3, (4, 6), 2))
        self.assertEqual(layout.mask_rows(2), (0, 1, 0, 0, 1, 0))

    def test_invalid_metadata_and_unverified_paths(self):
        geometry = ml.ModelGeometry(4, 6, 2, 2, 6, 0)
        for batch, options, sampling in ((6, {}, (3, (4, 6), 2)), (5, {"cond_or_uncond": [0, 1]}, (3, (4, 6), 2)),
            (6, {"cond_or_uncond": [0, 1], "uuids": [1]}, (3, (4, 6), 2)),
            (6, {"cond_or_uncond": [0, 1]}, None), (6, {"cond_or_uncond": [0, 1]}, (2, (4, 6), 2)),
            (6, {"cond_or_uncond": [0, 1]}, (3, (8, 6), 2)), (6, {"cond_or_uncond": [0, 2]}, (3, (4, 6), 2))):
            with self.subTest(options=options, sampling=sampling), self.assertRaises(ValueError):
                ml.call_layout(batch, options, geometry, sampling)

    def test_crop_and_integration_guards_precede_execution(self):
        region = ml.Krea2Context()
        def never(*args):
            self.fail("unsafe sampling reached executor")
        for options in ({"model_function_wrapper": object()}, {"context_handler": object()}, {"multigpu_clones": []}):
            with self.assertRaises(ValueError):
                region.conditioning_batch(never, None, [None], torch.zeros(1, 2, 4, 6), None, options)
        with self.assertRaisesRegex(ValueError, "crop"):
            region.conditioning_batch(never, None, [[{"area": (2, 2, 0, 0)}]], torch.zeros(1, 2, 4, 6), None, {})

    def test_exception_restores_nested_contexts(self):
        region = ml.Krea2Context()
        sentinel = object()
        region.current.set(sentinel)
        region.sampling.set(sentinel)
        def fail(*args):
            raise RuntimeError("test error")
        with self.assertRaisesRegex(RuntimeError, "test error"):
            run_region(region, fail)
        self.assertIs(region.current.get(), sentinel)
        self.assertIs(region.sampling.get(), sentinel)


class AlgebraTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(37)
        self.module = torch.nn.Linear(12, 6, bias=True)
        self.x = torch.randn(2, 7, 12) * .2

    def test_native_weight_parity_lora_and_all_lokr_forms(self):
        for alpha, strength in itertools.product((None, 0., 1., 7.), (0., 1., -.7, .35)):
            fixtures = [lora_weights(alpha=alpha)] + [lokr_weights(a, b, alpha) for a, b in itertools.product((False, True), repeat=2)]
            for weights in fixtures:
                adapter = parse(weights)
                ml.validate_adapter(adapter, self.module, "layer.weight")
                before = {k: v.clone() for k, v in weights.items()}
                base_weight = self.module.weight.detach().clone()
                reference = F.linear(self.x, reference_weight(self.module, [(adapter, strength)]), self.module.bias)
                normalized = ml.normalized_adapter(adapter, "cpu", torch.float32)
                bypass = self.module(self.x) + strength * normalized.h(self.x, self.module(self.x))
                with self.subTest(form=list(weights), alpha=alpha, strength=strength):
                    torch.testing.assert_close(bypass, reference, atol=1e-5, rtol=1e-4)
                    torch.testing.assert_close(self.module.weight, base_weight, atol=0, rtol=0)
                    for k, v in before.items():
                        torch.testing.assert_close(weights[k], v, atol=0, rtol=0)

    def test_soft_masks_negative_strength_and_chaining_exclude_text_references(self):
        adapters = [parse(lora_weights(alpha=3)), parse(lokr_weights(True, True, 7))]
        for image_only, mask_value, strength in itertools.product((False, True), (0., .4, 1.), (0., -.7)):
            region = ml.Krea2Context()
            additions = [contribution(a, torch.full((4, 6), mask_value), s) for a, s in zip(adapters, (strength, .3))]
            wrapped = ml.LayerDispatcher(self.module, additions, region, image_only)
            for refs, rank5 in itertools.product(([], [torch.zeros(1, 2, 2, 4)], [torch.zeros(1, 2, 2, 4), torch.zeros(1, 2, 3, 3)]), (False, True)):
                def inspect(*args):
                    geometry = region.current.get().geometry
                    start = 0 if image_only else 2
                    count = geometry.image_tokens
                    x = torch.randn(1, start + count + geometry.reference_tokens, 12) * .2
                    expected = self.module(x)
                    merged = reference_weight(self.module, list(zip(adapters, (strength * mask_value, .3 * mask_value))))
                    expected[:, start:start + count] = F.linear(x[:, start:start + count], merged, self.module.bias)
                    actual = wrapped(x)
                    torch.testing.assert_close(actual, expected, atol=1e-5, rtol=1e-4)
                    torch.testing.assert_close(actual[:, :start], self.module(x)[:, :start], atol=0, rtol=0)
                    torch.testing.assert_close(actual[:, start + count:], self.module(x)[:, start + count:], atol=0, rtol=0)
                run_region(region, inspect, refs=refs, rank5=rank5)

    def test_malformed_ranks_shapes_and_nonfinite_weights(self):
        bad = []
        weights = lora_weights(); weights["layer.lora_down.weight"] = torch.zeros(3, 12); bad.append(weights)
        weights = lora_weights(rank=0); bad.append(weights)
        weights = lora_weights(out=7); bad.append(weights)
        weights = lokr_weights(True, True, 1); weights["layer.lokr_w2_b"] = torch.zeros(2, 4); bad.append(weights)
        weights = lokr_weights(True, True, 1); del weights["layer.lokr_w1_b"]; bad.append(weights)
        weights = lora_weights(); weights["layer.lora_up.weight"][0, 0] = float("nan"); bad.append(weights)
        for weights in bad:
            with self.subTest(keys=list(weights)), self.assertRaises(ValueError):
                ml.validate_adapter(parse(weights), self.module, "layer.weight")

    def test_lokr_upstream_scaling_contract(self):
        ml.validate_lokr_api()
        from comfy.weight_adapter.lokr import LoKrAdapter
        with patch.object(LoKrAdapter, "calculate_weight", return_value=torch.zeros(4, 4)):
            with self.assertRaisesRegex(ValueError, "unsupported ComfyUI LoKr scaling API"):
                ml.validate_lokr_api()


class NativeInt8Tests(unittest.TestCase):
    def check_layer(self, device, dtype, atol, rtol):
        from comfy.quant_ops import QuantizedTensor
        torch.manual_seed(42)
        weight = torch.randn(256, 256, device=device, dtype=dtype) * .01
        quantized = QuantizedTensor.from_float(weight, "TensorWiseINT8Layout", per_channel=True, convrot=True, convrot_groupsize=256)
        layer = comfy.ops.mixed_precision_ops(compute_dtype=dtype).Linear(256, 256, bias=False, device=device)
        state = quantized.state_dict("weight")
        state["comfy_quant"] = torch.tensor(list(json.dumps({"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": 256}).encode()), dtype=torch.uint8)
        layer.load_state_dict(state)
        ml.validate_linear(layer, "native INT8 ConvRot")
        adapter = parse(lora_weights(out=256, inp=256, rank=3, alpha=5))
        region = ml.Krea2Context()
        x = torch.randn(1, 8, 256, device=device, dtype=dtype) * .1
        geometry = ml.ModelGeometry(4, 6, 2, 2, 6, 0)
        region.current.set(ml.CallLayout((0,), 1, (0,), geometry))
        original = layer.weight._qdata.clone()
        # Neither base execution nor the regional bypass may dequantize base weights.
        with torch.no_grad(), patch.object(QuantizedTensor, "dequantize", side_effect=AssertionError("base dequantized")):
            base = layer(x)
            dispatcher = ml.LayerDispatcher(layer.forward, (contribution(adapter, torch.full((4, 6), .4), -.7),), region,
                                            private_output=ml.native_private_output(layer, layer.forward))
            self.assertTrue(dispatcher.private_output)
            def sample():
                output = dispatcher(x)
                self.assertEqual(region.runtime.get().stats["inplace_outputs"], 1)
                self.assertEqual(region.runtime.get().stats["output_copies"], 0)
                return output
            output = region.outer_sample(sample)
        up, down = (v.to(device=device, dtype=torch.float32) for v in adapter.weights[:2])
        expected = base.float()
        expected[:, 2:] += F.linear(x[:, 2:].float(), up @ down) * (5 / 3) * -.7 * .4
        torch.testing.assert_close(output.float(), expected, atol=atol, rtol=rtol)
        torch.testing.assert_close(layer.weight._qdata, original, atol=0, rtol=0)
        torch.testing.assert_close(output[:, :2], base[:, :2], atol=0, rtol=0)
        return (output.float() - expected).abs().max().item()

    def test_cpu_native_int8_base_is_preserved(self):
        self.check_layer("cpu", torch.float32, 1e-5, 1e-4)

    @unittest.skipUnless(os.environ.get("WEPENERD_TEST_CUDA") == "1", "set WEPENERD_TEST_CUDA=1 for the GPU gate")
    def test_cuda_native_int8_base_is_preserved(self):
        self.assertTrue(torch.cuda.is_available(), "CUDA requested but unavailable")
        for dtype, atol, rtol in ((torch.float32, 1e-5, 1e-4), (torch.float16, 2e-4, 2e-3), (torch.bfloat16, 2e-3, 2e-2)):
            with self.subTest(dtype=dtype):
                error = self.check_layer("cuda", dtype, atol, rtol)
                print(f"Native INT8 ConvRot CUDA {dtype}: max absolute error={error:.8g}; atol={atol}, rtol={rtol}")


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        ml.FILE_CACHE.clear()
        self.addCleanup(ml.FILE_CACHE.clear)
        torch.manual_seed(13)
        config = comfy.supported_models.Krea2(dict(image_model="krea2", features=32, heads=2, kvheads=2,
            layers=1, patch=2, channels=2, multiplier=2, tdim=16, txtdim=16, txtlayers=1,
            txtheads=2, txtkvheads=2, dtype=torch.float32))
        base = comfy.model_base.Krea2(config, device=torch.device("cpu"))
        for param in base.parameters():
            torch.nn.init.uniform_(param, -.1, .1)
        self.model = comfy.model_patcher.ModelPatcher(base, torch.device("cpu"), torch.device("cpu"))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / "test.pt"
        self.weights = lora_weights("diffusion_model.first", 32, 8)
        self.weights.update(lora_weights("diffusion_model.blocks.0.attn.wq", 32, 32))
        self.weights.update(lora_weights("diffusion_model.last.linear", 8, 32))

    def load(self, source=None, weights=None, strength=1., mask=None, mask_data="", unique_id="test", **schedule):
        torch.save(self.weights if weights is None else weights, self.file)
        with patch.object(folder_paths, "get_filename_list", return_value=["test.pt"]), \
             patch.object(folder_paths, "get_full_path_or_raise", return_value=str(self.file)), \
             patch.object(ml, "save_asset", side_effect=AssertionError("LoRA load wrote an unused mask asset")):
            return ml.WepeNerdLoadLoraMasked().load(source or self.model, "test.pt", strength, mask_data, mask=mask, unique_id=unique_id, **schedule)

    def test_noop_output_and_external_override(self):
        for strength, mask in ((0, torch.rand(3, 7, 13)), (1, torch.zeros(2, 7, 13))):
            with patch.object(comfy.utils, "load_torch_file", side_effect=AssertionError("no-op read weights")):
                model, output = self.load(strength=strength, mask=mask, mask_data="invalid painting")
            torch.testing.assert_close(output, mask)
            self.assertFalse(model.object_patches)
            output.zero_()
            self.assertNotEqual(output.data_ptr(), mask.data_ptr())
        saved = document(np.array([[0, 128, 255]], np.uint8))
        _, output = self.load(strength=0, mask_data=saved)
        torch.testing.assert_close(output, torch.tensor([[[0., 128 / 255, 1.]]]))

    def test_legacy_schema_socket_and_widget_order(self):
        schema = ml.WepeNerdLoadLoraMasked.INPUT_TYPES()
        self.assertEqual(list(schema["required"]), ["model", "lora_name", "strength", "mask_data"])
        self.assertEqual(list(schema["optional"]), ["image", "mask", "start_percent", "end_percent", "apply_to"])
        self.assertEqual(ml.WepeNerdLoadLoraMasked.RETURN_TYPES, ("MODEL", "MASK"))
        self.assertEqual(schema["required"]["mask_data"][1]["default"], "")

    def test_preflight_mixed_unsupported_unmapped_and_no_spatial(self):
        cases = [({"omitted.dora_scale": torch.ones(1)}, "DoRA"),
                 ({"omitted.hada_w1_a": torch.ones(1, 1)}, "LoHa"),
                 ({"omitted.lokr_t2": torch.ones(1, 1)}, "Tucker"),
                 ({"omitted.reshape_weight": torch.tensor([3, 4])}, "reshape"),
                 ({"omitted.lora_up.weight": torch.ones(1, 1, 1, 1)}, "convolution"),
                 (lora_weights("unknown"), "unmapped")]
        for extra, message in cases:
            with self.subTest(message=message), patch.object(comfy.model_patcher.ModelPatcher, "add_object_patch") as add:
                with self.assertRaisesRegex(ValueError, message):
                    self.load(weights=self.weights | extra, mask=torch.ones(4, 6))
                add.assert_not_called()
                self.assertFalse(self.model.object_patches)
        with self.assertRaisesRegex(ValueError, "no supported spatial"):
            self.load(weights=lora_weights("diffusion_model.tmlp.0", 32, 16), mask=torch.ones(4, 6))
        for extra in (lora_weights("diffusion_model.last.linear", 9, 32),
                      {"diffusion_model.last.linear.lora_down.weight": torch.ones(7, 32)}):
            with patch.object(comfy.model_patcher.ModelPatcher, "add_object_patch") as add:
                with self.assertRaises(ValueError):
                    self.load(weights=self.weights | extra, mask=torch.ones(4, 6))
                add.assert_not_called()

    def test_duplicate_formats_and_aliases_fail_before_patching(self):
        weights = self.weights | {"diffusion_model.first.lokr_w1": torch.ones(2, 2),
                                  "diffusion_model.first.lokr_w2": torch.ones(16, 4)}
        with self.assertRaisesRegex(ValueError, "mixed LoRA/LoKr"):
            self.load(weights=weights, mask=torch.ones(4, 6))
        weights = self.weights | lora_weights("alias", 32, 8)
        mapping = comfy.lora.model_lora_keys_unet(self.model.model, {}) | {"alias": "diffusion_model.first.weight"}
        with self.assertRaisesRegex(ValueError, "duplicate adapter mappings"):
            ml.preflight_adapters(ml.parse_adapters(weights, "fixture"), mapping, self.model)

    def test_supported_omissions_and_nonadapter_metadata(self):
        weights = self.weights | lora_weights("diffusion_model.tmlp.0", 32, 16)
        weights.update({"metadata.description": "test", "optimizer.step": torch.tensor(3), "epoch": torch.tensor(1)})
        with self.assertLogs(level="INFO") as logs:
            result, _ = self.load(weights=weights, mask=torch.ones(4, 6))
        self.assertEqual(len(result.object_patches), 3)
        self.assertIn("1 supported text/nonspatial", " ".join(logs.output))

    def test_loader_boundaries(self):
        layer = torch.nn.Linear(12, 6)
        ml.validate_linear(layer, "test")
        for fmt in ("nvfp4", "float8_e4m3fn", "custom_int8"):
            layer.quant_format = fmt
            with self.assertRaisesRegex(ValueError, "native floating-point or INT8 ConvRot"):
                ml.validate_linear(layer, "test")
        class CustomLinear(torch.nn.Linear):
            pass
        with self.assertRaises(ValueError):
            ml.validate_linear(CustomLinear(12, 6), "custom/GGUF")

    def test_clone_regions_and_output_ownership(self):
        a, output = self.load(mask=torch.ones(4, 6), unique_id="a")
        b, _ = self.load(mask=torch.full((4, 6), .5), unique_id="b")
        chained, _ = self.load(source=a, mask=torch.full((4, 6), .2), unique_id="c")
        key = "diffusion_model.first.forward"
        self.assertIsNot(a.object_patches[key].context, b.object_patches[key].context)
        self.assertIs(chained.object_patches[key].original, a.object_patches[key].original)
        self.assertEqual(len(a.object_patches[key].contributions), 1)
        self.assertEqual(len(chained.object_patches[key].contributions), 2)
        self.assertFalse(self.model.object_patches)
        self.assertEqual(len(a.get_attachment("wepenerd_masked_loras")), 1)
        self.assertEqual(len(chained.get_attachment("wepenerd_masked_loras")), 2)
        output.zero_()
        self.assertTrue(a.object_patches[key].contributions[0].region.mask.all())

    def test_native_sampler_and_model_forward_ranks_padding_references(self):
        result, _ = self.load(mask=torch.stack([torch.zeros(5, 7), torch.ones(5, 7)]))
        region = result.object_patches["diffusion_model.first.forward"].context
        result.patch_model(load_weights=False)
        self.addCleanup(lambda: result.unpatch_model(unpatch_weights=False))
        result.model.current_patcher = result
        observed = []
        def record(executor, *args, **kwargs):
            # Runs inside our DIFFUSION_MODEL wrapper on the native model path.
            observed.append(region.current.get())
            return executor(*args, **kwargs)
        result.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "test_record", record)
        comfy.sampler_helpers.prepare_model_patcher(result, {}, result.model_options)
        for rank5, ref_count in itertools.product((False, True), (0, 1, 2)):
            shape = (3, 2, 1, 5, 7) if rank5 else (3, 2, 5, 7)
            x = torch.randn(shape) * .1
            refs = [torch.randn(1, 2, 3, 4) * .1 for _ in range(ref_count)]
            model_conds = {"c_crossattn": comfy.conds.CONDRegular(torch.randn(3, 2, 16) * .1)}
            if refs:
                model_conds.update(ref_latents=comfy.conds.CONDList(refs), ref_latents_method=comfy.conds.CONDConstant("index_timestep_zero"))
            cond = {"model_conds": model_conds, "uuid": "test"}
            output = region.outer_sample(lambda: comfy.samplers.calc_cond_batch(result.model, [[cond], [cond]], x, torch.ones(3), result.model_options))
            self.assertEqual([tuple(v.shape) for v in output], [shape, shape])
            self.assertTrue(all(torch.isfinite(v).all() for v in output))
            for layout in observed:
                self.assertEqual(layout.mask_rows(2), (0, 1, 0) * len(layout.chunk_labels))
                self.assertEqual(layout.geometry.image_tokens, 12)
            self.assertEqual(observed[-1].geometry.reference_tokens, ref_count * 4)
            observed.clear()
            self.assertIsNone(region.current.get())
            self.assertIsNone(region.sampling.get())


if __name__ == "__main__":
    unittest.main()
