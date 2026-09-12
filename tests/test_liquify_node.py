import base64
import io
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageOps
import torch

import comfy.cli_args
comfy.cli_args.args.cpu = True
import liquify_node as liquify


def encoded(image, format="PNG", **kwargs):
    stream = io.BytesIO()
    image.save(stream, format=format, **kwargs)
    return "data:image/" + format.lower().replace("jpg", "jpeg") + ";base64," + base64.b64encode(stream.getvalue()).decode()


def stroke():
    return {"radius": 0.28, "strength": 0.7, "points": [[0.35, 0.5], [0.5, 0.5], [0.6, 0.6]]}


class LiquifyTests(unittest.TestCase):
    def test_legacy_pixels_and_alpha_are_preserved(self):
        pixels = np.arange(9 * 13 * 4, dtype=np.uint8).reshape(9, 13, 4)
        image, mask = liquify.WN_LiquifyImage().process(encoded(Image.fromarray(pixels)))
        np.testing.assert_array_equal(image.numpy()[0], pixels[..., :3].astype(np.float32) / 255)
        np.testing.assert_array_equal(mask.numpy()[0], pixels[..., 3].astype(np.float32) / 255)

    def test_original_resolution_survives_file_and_reload(self):
        data = json.dumps({"v": 2, "source": encoded(Image.new("RGB", (2049, 73), "red")), "strokes": [stroke()]})
        first = liquify.WN_LiquifyImage().process(data)
        second = liquify.WN_LiquifyImage().process(data)
        self.assertEqual(first[0].shape, (1, 73, 2049, 3))
        self.assertTrue(torch.equal(first[0], second[0]))
        self.assertTrue(torch.equal(first[1], torch.ones_like(first[1])))

    def test_connected_image_takes_precedence_and_keeps_float_precision(self):
        source = torch.rand(2, 19, 23, 3)
        result = liquify.WN_LiquifyImage().process("invalid unused file data", image=source)
        self.assertTrue(torch.equal(result["result"][0], source))
        self.assertEqual(result["ui"]["liquify_source"][0]["batch"], 2)
        self.assertEqual(result["result"][1].shape, (2, 19, 23))

    def test_batched_warp_matches_individual_images(self):
        source = torch.rand(2, 143, 29, 4)
        actual = liquify.warp_image(source, [stroke()])
        expected = torch.cat([liquify.warp_image(sample[None], [stroke()]) for sample in source])
        self.assertTrue(torch.equal(actual, expected))
        self.assertFalse(torch.equal(actual, source))

    def test_warp_matches_independent_scalar_reference_across_strip_boundary(self):
        source = torch.rand(1, 139, 11, 3)
        steps = liquify.brush_dabs([stroke()], 11, 139)
        expected = source.numpy().copy()
        for y in range(139):
            for x in range(11):
                ox = oy = 0.0
                for cx, cy, radius, dx, dy in steps:
                    t = max(0, 1 - ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / radius)
                    weight = t * t * (3 - 2 * t)
                    ox -= dx * weight; oy -= dy * weight
                sx, sy = np.clip(x + ox, 0, 10), np.clip(y + oy, 0, 138)
                x0, y0 = int(sx), int(sy)
                fx, fy = sx - x0, sy - y0
                a = source.numpy()[0]
                expected[0, y, x] = (a[y0, x0] * (1 - fx) * (1 - fy)
                    + a[y0, min(x0 + 1, 10)] * fx * (1 - fy)
                    + a[min(y0 + 1, 138), x0] * (1 - fx) * fy
                    + a[min(y0 + 1, 138), min(x0 + 1, 10)] * fx * fy)
        np.testing.assert_allclose(liquify.warp_image(source, [stroke()]).numpy(), expected, atol=1e-5)

    def test_coordinates_scale_with_resolution(self):
        small = np.array(liquify.brush_dabs([stroke()], 160, 80))
        large = np.array(liquify.brush_dabs([stroke()], 1600, 800))
        np.testing.assert_allclose(large, small * 10)

    def test_unpainted_regions_are_exact_and_translated_brushes_match(self):
        image = torch.rand(1, 180, 320, 3)
        brush = {"radius": .05, "strength": .7, "points": [[.4, .5], [.45, .5]]}
        output = liquify.warp_image(image, [brush])
        self.assertTrue(torch.equal(image[:, :64], output[:, :64]))
        self.assertTrue(torch.equal(image[:, :, :100], output[:, :, :100]))
        crop = image[:, :, 64:256]
        shifted = {"radius": .05 * 320 / 192, "strength": .7,
                   "points": [[(x * 320 - 64) / 192, y] for x, y in brush["points"]]}
        cropped = liquify.warp_image(crop, [shifted])
        torch.testing.assert_close(output[:, :, 64:256], cropped, atol=1e-5, rtol=0)

    def test_redo_is_saved_but_not_rendered(self):
        data = json.dumps({"v": 2, "strokes": [], "redo": [stroke()]})
        source = torch.rand(1, 12, 18, 3)
        result = liquify.WN_LiquifyImage().process(data, image=source)
        self.assertTrue(torch.equal(result["result"][0], source))
        self.assertEqual(liquify.parse_document(data)["redo"], [stroke()])

    def test_preview_is_bounded_but_output_is_full_size(self):
        source = torch.rand(1, 97, 2200, 3)
        result = liquify.WN_LiquifyImage().process("", image=source)
        preview = result["ui"]["liquify_source"][0]
        decoded = liquify.decode_source(preview["data"])
        self.assertEqual(decoded.shape[2], 1536)
        self.assertEqual((preview["width"], preview["height"]), (2200, 97))
        self.assertEqual(result["result"][0].shape, source.shape)

    def test_disconnected_input_does_not_render_low_resolution_preview(self):
        with self.assertRaisesRegex(ValueError, "IMAGE connection"):
            liquify.WN_LiquifyImage().process(json.dumps({"v": 2, "source_mode": "input", "strokes": []}))

    def test_bad_state_and_oversized_images_fail_clearly(self):
        for payload in ('{"v":9}', '{"v":2,"strokes":{}}', '{"v":2,"strokes":[{"radius":NaN}]}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                liquify.parse_document(payload)
        with self.assertRaises(ValueError): liquify.decode_source("not an image")
        with patch.object(liquify, "MAX_PIXELS", 8), self.assertRaisesRegex(ValueError, "megapixels"):
            liquify.decode_source(encoded(Image.new("RGB", (3, 3))))

    def test_exif_orientation_is_applied(self):
        source = Image.fromarray(np.arange(11 * 7 * 3, dtype=np.uint8).reshape(11, 7, 3))
        exif = Image.Exif(); exif[274] = 6
        value = encoded(source, "JPEG", exif=exif)
        expected = ImageOps.exif_transpose(Image.open(io.BytesIO(base64.b64decode(value.split(",")[1])))).convert("RGB")
        result = liquify.decode_source(value)
        np.testing.assert_array_equal(result[0, ..., :3].numpy(), np.array(expected).astype(np.float32) / 255)

    def test_cancellation_is_checked_between_strips(self):
        with patch.object(liquify, "throw_exception_if_processing_interrupted", side_effect=RuntimeError("cancelled")):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                liquify.warp_image(torch.zeros(1, 260, 10, 3), [stroke()])

    def test_legacy_required_input_and_outputs_remain(self):
        self.assertEqual(list(liquify.WN_LiquifyImage.INPUT_TYPES()["required"]), ["image_data"])
        self.assertEqual(liquify.WN_LiquifyImage.RETURN_TYPES, ("IMAGE", "MASK"))
        self.assertEqual(liquify.WN_LiquifyImage.INPUT_TYPES()["optional"]["image"][0], "IMAGE")


if __name__ == "__main__": unittest.main()
