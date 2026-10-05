"""Run with ComfyUI's Python: python -m unittest discover -s tests -p test_qwen_edit_mask.py."""

import base64
import io
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
package = types.ModuleType("wepenerd_qwen_mask_test")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
from wepenerd_qwen_mask_test.qwen_edit_mask_node import WN_QwenEditMask, qwen_size, region_prompt


def document(alpha, source=None):
    rgba = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    rgba[..., :3] = (216, 77, 157)
    rgba[..., 3] = alpha
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    data = {"v": 1, "width": alpha.shape[1], "height": alpha.shape[0],
            "png": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()}
    if source is not None:
        data["source"] = source
    return json.dumps(data)


def embedded(pixels):
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def square(h, w, top, left, size):
    alpha = np.zeros((h, w), dtype=np.uint8)
    alpha[top:top + size, left:left + size] = 255
    return alpha


class QwenSizeTests(unittest.TestCase):
    def reference(self, width, height, resolution):
        # Verbatim rule from ComfyUI's TextEncodeQwenImage21.
        if resolution > 0:
            ratio = width / height
            w = round(math.sqrt(resolution * resolution * ratio) / 32) * 32
            h = round(math.sqrt(resolution * resolution / ratio) / 32) * 32
        else:
            w, h = round(width / 32) * 32, round(height / 32) * 32
        return max(32, w), max(32, h)

    def test_matches_encoder_rule(self):
        for width, height in [(1000, 700), (1024, 1024), (1920, 1080), (1040, 720), (17, 3000), (3, 5)]:
            for resolution in (0, 512, 1024, 2048):
                self.assertEqual(qwen_size(width, height, resolution), self.reference(width, height, resolution))

    def test_output_is_stable_through_encoder_at_resolution_zero(self):
        w, h = qwen_size(1000, 700, 1024)
        self.assertEqual((w % 32, h % 32), (0, 0))
        self.assertEqual(self.reference(w, h, 0), (w, h))


class QwenEditMaskTests(unittest.TestCase):
    def run_node(self, alpha, image, **kwargs):
        with patch("wepenerd_qwen_mask_test.qwen_edit_mask_node.save_asset",
                   return_value={"filename": "preview.png", "subfolder": "", "type": "input"}):
            result = WN_QwenEditMask().build(document(alpha), image=image, **kwargs)
        self.assertEqual(result["ui"]["paint_mask_source"][0]["filename"], "preview.png")
        return result["result"]

    def test_outline_hugs_region_from_outside(self):
        image = torch.full((1, 64, 64, 3), 0.5)
        guided, clean, mask, mask_image, prompt = self.run_node(
            square(64, 64, 16, 16, 32), image, guide="outline", color="red", line_px=3, resolution=0)
        self.assertEqual(tuple(guided.shape), (1, 64, 64, 3))
        torch.testing.assert_close(clean, image)
        # Inside the region and far outside are untouched; the ring just outside is red.
        torch.testing.assert_close(guided[0, 20:44, 20:44], image[0, 20:44, 20:44])
        torch.testing.assert_close(guided[0, :10, :10], image[0, :10, :10])
        torch.testing.assert_close(guided[0, 14, 30], torch.tensor([1.0, 0.0, 0.0]))
        torch.testing.assert_close(guided[0, 30, 49], torch.tensor([1.0, 0.0, 0.0]))
        self.assertTrue(torch.all(mask[0, 16:48, 16:48] == 1))
        self.assertEqual(float(mask.sum()), 32 * 32)
        torch.testing.assert_close(mask_image[..., 0], mask)
        self.assertIn("inside the red outline", prompt)

    def test_tint_and_solid(self):
        image = torch.zeros((1, 32, 32, 3))
        alpha = square(32, 32, 8, 8, 16)
        tinted = self.run_node(alpha, image, guide="tint", color="green", resolution=0)[0]
        torch.testing.assert_close(tinted[0, 16, 16], torch.tensor([0.0, 0.5, 0.0]))
        torch.testing.assert_close(tinted[0, 0, 0], torch.zeros(3))
        solid = self.run_node(alpha, image + 1, guide="solid", color="blue", resolution=0)[0]
        torch.testing.assert_close(solid[0, 16, 16], torch.tensor([0.0, 0.25, 1.0]))

    def test_none_guide_leaves_image_clean(self):
        image = torch.rand(1, 32, 32, 3)
        guided, clean, mask, mask_image, prompt = self.run_node(square(32, 32, 0, 0, 10), image,
                                                               guide="none (use mask_image)", resolution=0)
        torch.testing.assert_close(guided, image)
        self.assertIn("<image2>", prompt)

    def test_resizes_to_qwen_grid_and_batches(self):
        image = torch.rand(2, 70, 100, 3)
        guided, clean, mask, mask_image, _ = self.run_node(square(70, 100, 10, 10, 30), image, resolution=64)
        size = qwen_size(100, 70, 64)
        for tensor in (guided, clean, mask_image):
            self.assertEqual(tuple(tensor.shape), (2, size[1], size[0], 3))
        self.assertEqual(tuple(mask.shape), (2, size[1], size[0]))
        torch.testing.assert_close(mask[0], mask[1])

    def test_rgba_alpha_follows_guide(self):
        image = torch.zeros((1, 32, 32, 4))
        guided = self.run_node(square(32, 32, 8, 8, 16), image, guide="solid", resolution=0)[0]
        self.assertEqual(float(guided[0, 16, 16, 3]), 1.0)
        self.assertEqual(float(guided[0, 0, 0, 3]), 0.0)

    def test_mask_painted_at_other_size_is_fitted(self):
        image = torch.zeros((1, 64, 64, 3))
        mask = self.run_node(square(32, 32, 0, 0, 16), image, resolution=0)[2]
        self.assertTrue(torch.all(mask[0, 4:28, 4:28] > 0.99))
        self.assertTrue(torch.all(mask[0, 36:, 36:] == 0))

    def test_loop_selects_enclosed_area_unless_disabled(self):
        image = torch.zeros((1, 64, 64, 3))
        loop = square(64, 64, 10, 10, 40)
        loop[16:44, 16:44] = 0
        filled = self.run_node(loop, image, resolution=0)[2]
        self.assertTrue(torch.all(filled[0, 10:50, 10:50] == 1))
        literal = self.run_node(loop, image, resolution=0, fill_holes=False)[2]
        self.assertTrue(torch.all(literal[0, 16:44, 16:44] == 0))

    def test_empty_painting_changes_nothing(self):
        image = torch.rand(1, 32, 32, 3)
        guided = self.run_node(np.zeros((32, 32), dtype=np.uint8), image, guide="outline", resolution=0)[0]
        torch.testing.assert_close(guided, image)

    def test_opened_image_is_used_without_connection(self):
        pixels = np.full((32, 64, 3), 100, dtype=np.uint8)
        saved = document(square(32, 64, 0, 0, 8), source=embedded(pixels))
        guided, clean, mask, _, _ = WN_QwenEditMask().build(saved, guide="solid", resolution=0)
        self.assertEqual(tuple(clean.shape), (1, 32, 64, 3))
        torch.testing.assert_close(clean[0, 20, 40], torch.full((3,), 100 / 255))
        torch.testing.assert_close(guided[0, 2, 2], torch.tensor([1.0, 0.0, 0.0]))

    def test_missing_image_is_reported(self):
        with self.assertRaisesRegex(ValueError, "open or drop an image"):
            WN_QwenEditMask().build(document(np.zeros((8, 8), dtype=np.uint8)))

    def test_prompt_wraps_instruction(self):
        self.assertEqual(region_prompt("outline", "red", "  replace it with a cat.  "),
                         "In <image1>, edit only the area inside the red outline: replace it with a cat. "
                         "Remove the red outline. Keep everything outside that area unchanged.")
        self.assertTrue(region_prompt("solid", "blue", "").startswith("In <image1>, edit only the area painted solid blue."))


if __name__ == "__main__":
    unittest.main()
