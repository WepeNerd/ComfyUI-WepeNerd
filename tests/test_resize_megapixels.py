import sys
from pathlib import Path
import unittest

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
sys.path.insert(0, str(ROOT))
from resize_megapixels_node import WN_ResizeMegapixels, megapixel_dimensions


class ResizeMegapixelsTests(unittest.TestCase):
    def test_decimal_megapixels(self):
        self.assertEqual(megapixel_dimensions(512, 512, 1, 2), (1000, 1000))

    def test_divisibility_and_nearest_dimensions(self):
        for width, height in ((1920, 1080), (1080, 1920), (997, 997), (4000, 1)):
            for divisor in (2, 4, 8, 16, 32, 64):
                for mp in (0.01, 0.5, 1, 4):
                    with self.subTest(size=(width, height), divisor=divisor, mp=mp):
                        result = megapixel_dimensions(width, height, mp, divisor)
                        scale = (mp * 1_000_000 / (width * height)) ** 0.5
                        for actual, source in zip(result, (width, height)):
                            self.assertEqual(actual % divisor, 0)
                            self.assertGreaterEqual(actual, divisor)
                            if source * scale >= divisor:
                                self.assertLessEqual(abs(actual - source * scale), divisor / 2 + 1e-8)
                        self.assertEqual(
                            result[::-1], megapixel_dimensions(height, width, mp, divisor)
                        )

    def test_invalid_target(self):
        for target in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                megapixel_dimensions(100, 100, target, 8)

    def test_all_methods_resize_batches_up_and_down(self):
        node = WN_ResizeMegapixels()
        image = torch.stack((torch.zeros(64, 128, 3), torch.ones(64, 128, 3)))
        for method in node.UPSCALE_METHODS:
            for mp, expected in ((0.002048, (2, 32, 64, 3)), (0.032768, (2, 128, 256, 3))):
                with self.subTest(method=method, mp=mp):
                    output, = node.resize(image, mp, "8", method)
                    self.assertEqual(tuple(output.shape), expected)
                    self.assertEqual(output.dtype, image.dtype)
                    self.assertTrue(torch.isfinite(output).all())
                    torch.testing.assert_close(output[0], torch.zeros_like(output[0]))
                    torch.testing.assert_close(output[1], torch.ones_like(output[1]))

    def test_no_op_preserves_values(self):
        image = torch.rand(2, 64, 128, 3)
        output, = WN_ResizeMegapixels().resize(image, 0.008192, "8", "lanczos")
        self.assertIs(output, image)


if __name__ == "__main__":
    unittest.main()
