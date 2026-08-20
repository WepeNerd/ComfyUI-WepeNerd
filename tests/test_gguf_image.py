import base64
import importlib
import io
import unittest

import numpy as np
from PIL import Image


images = importlib.import_module("wepenerd_testpkg.wn_gguf_image")


def decode_url(url):
    prefix, encoded = url.split(",", 1)
    return prefix, Image.open(io.BytesIO(base64.b64decode(encoded)))


class ImageTests(unittest.TestCase):
    def test_batch_one_returns_one_jpeg(self):
        urls = images.encode_image_batch(np.zeros((1, 8, 8, 3), dtype=np.float32))
        self.assertEqual(len(urls), 1)
        prefix, image = decode_url(urls[0])
        self.assertEqual(prefix, "data:image/jpeg;base64")
        self.assertEqual(image.format, "JPEG")

    def test_batch_more_than_one_is_preserved(self):
        urls = images.encode_image_batch(np.zeros((3, 8, 8, 3), dtype=np.float32))
        self.assertEqual(len(urls), 3)
        with self.assertRaisesRegex(ValueError, "batch of 3"):
            images.comfy_image_to_data_url(np.zeros((3, 8, 8, 3), dtype=np.float32))

    def test_resize_max_edge_without_upscaling(self):
        _, large = decode_url(images.encode_single_image(np.zeros((100, 200, 3)), max_edge=80))
        _, small = decode_url(images.encode_single_image(np.zeros((20, 40, 3)), max_edge=80))
        self.assertEqual(large.size, (80, 40))
        self.assertEqual(small.size, (40, 20))

    def test_rgba_converts_to_rgb_for_jpeg(self):
        value = np.zeros((8, 8, 4), dtype=np.float32)
        value[..., 3] = 0.5
        _, image = decode_url(images.encode_single_image(value))
        self.assertEqual(image.mode, "RGB")

    def test_png_remains_available_to_backward_helper(self):
        url = images.comfy_image_to_data_url(
            np.zeros((1, 8, 8, 3), dtype=np.float32), fmt="PNG"
        )
        prefix, image = decode_url(url)
        self.assertEqual(prefix, "data:image/png;base64")
        self.assertEqual(image.format, "PNG")
