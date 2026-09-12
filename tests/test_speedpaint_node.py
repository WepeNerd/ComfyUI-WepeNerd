import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch
from PIL import Image, ImageCms, ImageOps

import speedpaint_node as sp


def image_bytes(image, format="PNG", **kwargs):
    output = io.BytesIO()
    image.save(output, format=format, **kwargs)
    return output.getvalue()


class SpeedpaintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.asset_patch = patch.object(sp, "asset_root", return_value=self.root)
        self.asset_patch.start()
        self.addCleanup(self.asset_patch.stop)
        y, x = np.mgrid[:180, :320]
        self.source = Image.fromarray(np.stack((x % 256, y % 256, (x + y) % 256), axis=-1).astype(np.uint8))

    def test_blank_rgb_tensor_exact_dimensions(self):
        result = sp.WN_Speedpaint().render(113, 257, json.dumps({"v": 1, "background": "#127fdf"}))
        tensor = result["result"][0]
        self.assertEqual(tensor.shape, (1, 257, 113, 3))
        self.assertEqual(tensor.dtype, torch.float32)
        self.assertTrue(torch.equal(tensor[0, 0, 0], torch.tensor([18, 127, 223], dtype=torch.float32) / 255))
        self.assertTrue(torch.equal(tensor, tensor[:, :1, :1, :].expand_as(tensor)))

    def test_import_crop_matches_lanczos_in_both_orientations(self):
        for source in (self.source, self.source.transpose(Image.Transpose.ROTATE_90)):
            for size in ((81, 161), (211, 73)):
                with self.subTest(source=source.size, target=size):
                    document = sp.import_document(image_bytes(source), *size, "#ffffff")
                    expected = ImageOps.fit(source.convert("RGBA"), size, method=Image.Resampling.LANCZOS).convert("RGB")
                    self.assertEqual(sp.read_asset(document["asset"]).convert("RGB").tobytes(), expected.tobytes())
                    output = sp.prepare_image(document, *size)
                    self.assertEqual(output.tobytes(), expected.tobytes())

    def test_exif_orientation_before_crop(self):
        exif = Image.Exif(); exif[274] = 6
        raw = image_bytes(self.source, "JPEG", exif=exif)
        document = sp.import_document(raw, 91, 153, "#ffffff")
        expected = ImageOps.fit(ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGBA"), (91, 153), method=Image.Resampling.LANCZOS).convert("RGB")
        self.assertEqual((document["source_width"], document["source_height"]), (180, 320))
        self.assertEqual(sp.prepare_image(document, 91, 153).tobytes(), expected.tobytes())

    def test_transparency_composites_over_selected_background(self):
        image = Image.new("RGBA", (64, 64), (200, 40, 20, 128))
        document = sp.import_document(image_bytes(image), 64, 64, "#102030")
        expected = Image.alpha_composite(Image.new("RGBA", (64, 64), (16, 32, 48, 255)), image).convert("RGB")
        self.assertEqual(sp.prepare_image(document, 64, 64).tobytes(), expected.tobytes())

    def test_embedded_srgb_profile_and_invalid_profile(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        image = sp.decode_image(image_bytes(self.source, icc_profile=profile))
        self.assertEqual(image.convert("RGB").tobytes(), self.source.tobytes())
        with self.assertRaises((OSError, ImageCms.PyCMSError)):
            sp.decode_image(image_bytes(self.source, icc_profile=b"invalid profile"))

    def test_reposition_uses_original_source(self):
        document = sp.import_document(image_bytes(self.source), 64, 128, "#ffffff")
        document["crop"] = [1, .5]
        expected = ImageOps.fit(self.source.convert("RGBA"), (64, 128), method=Image.Resampling.LANCZOS, centering=(1, .5)).convert("RGB")
        self.assertEqual(sp.prepare_image(document, 64, 128).tobytes(), expected.tobytes())
        self.assertEqual(sp.asset_path(document["source"]).read_bytes(), image_bytes(self.source))

    def painted_document(self):
        document = sp.import_document(image_bytes(self.source), 256, 128, "#ffffff")
        composed = sp.prepare_image(document, 256, 128)
        composed.paste((255, 0, 128), (80, 20, 120, 50))
        document.update(painted=True, inline="data:image/png;base64," + base64.b64encode(image_bytes(composed)).decode())
        return document, composed

    def test_execution_resizes_paint_and_source_together_without_mutation(self):
        document, composed = self.painted_document()
        serialized = json.dumps(document)
        for size in ((64, 128), (256, 128), (384, 96), (256, 128)):
            expected = ImageOps.fit(composed.convert("RGBA"), size, method=Image.Resampling.LANCZOS).convert("RGB")
            self.assertEqual(sp.prepare_image(document, *size).tobytes(), expected.tobytes())
        self.assertEqual(json.dumps(document), serialized)

    def test_commit_is_lossless_immutable_and_independent(self):
        document, composed = self.painted_document()
        committed = sp.commit_document(document)
        self.assertNotIn("inline", committed)
        self.assertEqual(sp.read_asset(committed["asset"]).convert("RGB").tobytes(), composed.tobytes())
        self.assertEqual(committed["asset"], sp.commit_document(document)["asset"])
        composed.paste((0, 0, 0), (0, 0, 10, 10))
        document["inline"] = "data:image/png;base64," + base64.b64encode(image_bytes(composed)).decode()
        changed = sp.commit_document(document)
        self.assertNotEqual(committed["asset"], changed["asset"])
        self.assertNotEqual(sp.read_asset(committed["asset"]).tobytes(), sp.read_asset(changed["asset"]).tobytes())

    def test_inline_recovery_and_committed_output_match(self):
        document, composed = self.painted_document()
        committed = sp.commit_document(document)
        first = sp.WN_Speedpaint().render(256, 128, json.dumps(document))
        second = sp.WN_Speedpaint().render(256, 128, json.dumps(committed))
        self.assertTrue(torch.equal(first["result"][0], second["result"][0]))
        np.testing.assert_array_equal((first["result"][0][0].numpy() * 255).round().astype(np.uint8), np.asarray(composed))

    def test_invalid_dimensions_and_crop_are_rejected(self):
        for size in ((63, 128), (128, 4097), (128.5, 128), (True, 128), (128, "128")):
            with self.subTest(size=size), self.assertRaises(ValueError): sp.validate_size(*size)
        for crop in ([float("nan"), .5], [2, .5], "crop", [0]):
            with self.subTest(crop=crop), self.assertRaises(ValueError): sp.parse_document({"v": 1, "crop": crop})

    def test_paths_decode_and_canvas_integrity(self):
        for name in ("../private.png", "C:\\private.png", "x" * 64 + ".png", "a" * 64 + ".png/../x"):
            with self.assertRaises(ValueError): sp.asset_path(name)
        with self.assertRaises((OSError, ValueError)): sp.decode_image(b"not an image")
        document, _ = self.painted_document()
        document["width"] = 128
        with self.assertRaisesRegex(ValueError, "dimensions"): sp.commit_document(document)
        document["width"] = 256
        document["inline"] = "data:image/png;base64,INVALID!"
        with self.assertRaises(ValueError): sp.commit_document(document)

    def test_no_paint_resize_regenerates_original(self):
        document = sp.import_document(image_bytes(self.source), 64, 128, "#ffffff")
        resized = sp.prepare_document(document, 320, 180)
        self.assertEqual(sp.prepare_image(resized, 320, 180).tobytes(), self.source.tobytes())


if __name__ == "__main__":
    unittest.main()
