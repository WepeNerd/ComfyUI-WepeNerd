"""Run with ComfyUI's Python: python -m unittest discover -s tests -p test_paint_mask.py."""

import base64
import io
import json
from pathlib import Path
import sys
import types
import unittest
import tempfile
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
package = types.ModuleType("wepenerd_mask_test")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
from wepenerd_mask_test.paint_mask_node import WN_PaintMask


def document(alpha):
    rgba = np.zeros((*alpha.shape, 4), dtype=np.uint8)
    rgba[..., :3] = (216, 77, 157)  # Display colour must never determine coverage.
    rgba[..., 3] = alpha
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    return json.dumps({"v": 1, "width": alpha.shape[1], "height": alpha.shape[0],
                       "png": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()})


class PaintMaskTests(unittest.TestCase):
    def test_coverage_and_native_geometry(self):
        alpha = np.array([[0, 128, 255], [255, 0, 64]], dtype=np.uint8)
        mask, image = WN_PaintMask().paint(document(alpha))
        self.assertEqual(tuple(mask.shape), (1, 2, 3))
        self.assertEqual(mask.dtype, torch.float32)
        np.testing.assert_allclose(mask[0].numpy(), alpha.astype(np.float32) / 255)

    def test_clear_keeps_non_square_dimensions(self):
        saved = json.loads(document(np.zeros((17, 31), dtype=np.uint8)))
        saved["empty"] = True
        mask, image = WN_PaintMask().paint(json.dumps(saved))
        self.assertEqual(tuple(mask.shape), (1, 17, 31))
        self.assertFalse(mask.any())

    def test_initial_canvas_is_empty(self):
        mask, image = WN_PaintMask().paint()
        self.assertEqual(tuple(mask.shape), (1, 1024, 1024))
        self.assertFalse(mask.any())
        self.assertEqual(tuple(image.shape), (1, 1024, 1024, 3))

    def test_opened_image_is_returned_without_mask_overlay(self):
        pixels = np.array([[[10, 20, 30], [70, 80, 90]]], dtype=np.uint8)
        buffer = io.BytesIO()
        Image.fromarray(pixels).save(buffer, format="PNG")
        saved = json.loads(document(np.array([[255, 0]], dtype=np.uint8)))
        saved["source"] = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
        mask, image = WN_PaintMask().paint(json.dumps(saved))
        np.testing.assert_allclose(image[0].numpy(), pixels.astype(np.float32) / 255)
        self.assertEqual(mask.tolist(), [[[1, 0]]])

    @patch("wepenerd_mask_test.paint_mask_node.save_asset", return_value={"filename": "preview.png", "subfolder": "", "type": "input"})
    def test_connected_batch_passes_through_and_mask_fits(self, save):
        image = torch.rand(2, 4, 6, 3)
        saved = document(np.array([[255, 255, 0], [255, 255, 0]], dtype=np.uint8))
        result = WN_PaintMask().paint(saved, image=image)
        mask, output = result["result"]
        self.assertIs(output, image)
        self.assertEqual(tuple(mask.shape), (2, 4, 6))
        torch.testing.assert_close(mask[0], mask[1])
        self.assertTrue(torch.all(mask[:, :, 0] == 1))
        self.assertTrue(torch.all(mask[:, :, -1] == 0))
        self.assertEqual(result["ui"]["paint_mask_source"][0]["filename"], "preview.png")

    def test_geometry_mismatch_is_rejected(self):
        saved = json.loads(document(np.zeros((2, 3), dtype=np.uint8)))
        saved["width"] = 4
        with self.assertRaisesRegex(ValueError, "Paint Mask"):
            WN_PaintMask().paint(json.dumps(saved))

    def test_asset_source_matches_legacy_embedded_source(self):
        pixels = np.array([[[15, 25, 35], [80, 90, 100]]], dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "wepenerd_paint_mask").mkdir()
            Image.fromarray(pixels).save(root / "wepenerd_paint_mask" / "source.png")
            saved = json.loads(document(np.array([[255, 0]], dtype=np.uint8)))
            saved["source"] = {"name": "source.png", "subfolder": "wepenerd_paint_mask", "type": "input"}
            with patch("wepenerd_mask_test.paint_mask_node.folder_paths.get_input_directory", return_value=directory):
                mask, image = WN_PaintMask().paint(json.dumps(saved))
            np.testing.assert_allclose(image[0].numpy(), pixels.astype(np.float32) / 255)
            self.assertEqual(mask.tolist(), [[[1, 0]]])

    def test_asset_cannot_escape_input_directory(self):
        saved = json.loads(document(np.zeros((2, 3), dtype=np.uint8)))
        saved["source"] = {"filename": "outside.png", "subfolder": "..", "type": "input"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("wepenerd_mask_test.paint_mask_node.folder_paths.get_input_directory", return_value=directory):
                with self.assertRaisesRegex(ValueError, "outside the input folder"):
                    WN_PaintMask().paint(json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
