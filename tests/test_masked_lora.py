import base64
import io
import json
import unittest
from types import SimpleNamespace

import numpy as np
import torch
from PIL import Image

from masked_lora_node import PREFIX, RegionContext, RegionalForward, decode_mask, validate_adapter


def mask_json(array):
    image = Image.fromarray(np.asarray(array, dtype=np.uint8))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return json.dumps({"v": 1, "width": image.width, "height": image.height,
                       "png": PREFIX + base64.b64encode(output.getvalue()).decode()})


class LinearAdapter:
    name = "test"

    def __init__(self):
        self.weights = (torch.eye(3),)

    def h(self, x, output):
        return torch.nn.functional.linear(x, self.weights[0])


class MaskedLoraTests(unittest.TestCase):
    def test_lossless_geometry_and_cache_identity(self):
        mask, digest = decode_mask(mask_json([[0, 1, 127], [128, 254, 255]]))
        self.assertEqual(mask.shape, (1, 1, 2, 3))
        self.assertTrue(torch.equal((mask * 255).byte()[0, 0], torch.tensor([[0, 1, 127], [128, 254, 255]], dtype=torch.uint8)))
        self.assertNotEqual(digest, decode_mask(mask_json([[0, 1, 127], [128, 254, 254]]))[1])

    def test_invalid_geometry_fails(self):
        data = json.loads(mask_json([[255]])); data["width"] = 2
        with self.assertRaisesRegex(ValueError, "geometry"):
            decode_mask(json.dumps(data))

    def test_alpha_coverage_not_preview_color(self):
        mask, _ = decode_mask(mask_json([[[255, 255, 255, 0], [255, 255, 255, 128]]]))
        self.assertEqual(mask[0, 0, 0, 0], 0)
        self.assertAlmostEqual(mask[0, 0, 0, 1].item(), 128 / 255)

    def test_spatial_support_text_and_reference_exclusion(self):
        region = RegionContext(torch.zeros(1, 1, 2, 2))
        region.current.set((2, 4, 3, torch.tensor([0., 1., .5, 0.]).view(1, 4, 1)))
        x = torch.ones(2, 9, 3)
        forward = RegionalForward(lambda x: x * 2, LinearAdapter(), region, -2)
        delta = forward(x) - x * 2
        self.assertEqual(torch.count_nonzero(delta[:, :2]), 0)
        self.assertEqual(torch.count_nonzero(delta[:, 6:]), 0)
        self.assertEqual(torch.count_nonzero(delta[:, [2, 5]]), 0)
        torch.testing.assert_close(delta[:, 3], torch.full((2, 3), -2.))

    def test_two_regions_overlap_add_and_global_base_survives(self):
        a, b = RegionContext(None), RegionContext(None)
        a.current.set((0, 3, 0, torch.tensor([1., 1., 0.]).view(1, 3, 1)))
        b.current.set((0, 3, 0, torch.tensor([0., 1., 1.]).view(1, 3, 1)))
        first = RegionalForward(lambda x: x * 7, LinearAdapter(), a, 2)
        second = RegionalForward(first, LinearAdapter(), b, -1)
        actual = second(torch.ones(1, 3, 3))
        torch.testing.assert_close(actual, torch.tensor([9., 8., 6.]).view(1, 3, 1).expand(1, 3, 3))

    def test_zero_strength_and_black_mask(self):
        for strength, value in [(0, 1), (1, 0)]:
            region = RegionContext(None)
            region.current.set((0, 4, 0, torch.full((1, 4, 1), float(value))))
            x = torch.randn(1, 4, 3)
            torch.testing.assert_close(RegionalForward(lambda x: x, LinearAdapter(), region, strength)(x), x, rtol=0, atol=0)

    def test_layout_validation(self):
        region = RegionContext(None)
        region.current.set((0, 4, 0, torch.ones(1, 4, 1)))
        with self.assertRaisesRegex(ValueError, "token layout"):
            RegionalForward(lambda x: x, LinearAdapter(), region, 1)(torch.ones(1, 5, 3))

    def test_wrapper_cleanup_on_exception_and_padded_grid(self):
        region = RegionContext(torch.ones(1, 1, 2, 3))
        class Executor:
            class_obj = SimpleNamespace(patch=2, default_ref_method=None)
            def __call__(self, *args, **kwargs):
                text, count, refs, mask = region.current.get()
                self_test.assertEqual((text, count, refs), (3, 6, 0))
                self_test.assertEqual(mask.shape, (1, 6, 1))
                raise RuntimeError("deliberate")
        self_test = self
        with self.assertRaisesRegex(RuntimeError, "deliberate"):
            region(Executor(), torch.zeros(1, 16, 3, 5), torch.ones(1), torch.zeros(1, 3, 64))
        self.assertIsNone(region.current.get())

    def test_unsupported_adapter_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "unsupported adapter"):
            validate_adapter(SimpleNamespace(name="loha"), torch.nn.Linear(3, 3), "layer")


if __name__ == "__main__":
    unittest.main()
