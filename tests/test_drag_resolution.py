import importlib.util
import math
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "drag_resolution.py"
SPEC = importlib.util.spec_from_file_location("wn_drag_resolution_math", MODULE_PATH)
resolution = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolution)


class DragResolutionMathTests(unittest.TestCase):
    def assert_valid(self, result, divisor):
        width, height = result
        self.assertEqual(width % divisor, 0)
        self.assertEqual(height % divisor, 0)
        self.assertGreaterEqual(width, 64)
        self.assertGreaterEqual(height, 64)
        self.assertLessEqual(width, 8192)
        self.assertLessEqual(height, 8192)

    def test_square_one_megapixel(self):
        result = resolution.find_best_resolution(1.0, 1.0, 32)
        self.assert_valid(result, 32)
        self.assertEqual(result[0], result[1])
        self.assertLess(abs(resolution.resolution_megapixels(*result) - 1.0), 0.05)

    def test_landscape_and_portrait_are_corresponding(self):
        landscape = resolution.find_best_resolution(1.0, 16 / 9, 32)
        portrait = resolution.find_best_resolution(1.0, 9 / 16, 32)
        self.assert_valid(landscape, 32)
        self.assert_valid(portrait, 32)
        self.assertEqual(landscape, tuple(reversed(portrait)))
        self.assertLess(abs(math.log((landscape[0] / landscape[1]) / (16 / 9))), 0.03)
        self.assertLess(abs(resolution.resolution_megapixels(*landscape) - 1.0), 0.05)

    def test_supported_divisors(self):
        for divisor in (8, 16, 32, 64):
            with self.subTest(divisor=divisor):
                result = resolution.find_best_resolution(1.0, 16 / 9, divisor)
                self.assert_valid(result, divisor)
                self.assertLess(abs(resolution.resolution_megapixels(*result) - 1.0), 0.06)

    def test_free_ratio_source_can_preserve_current_shape(self):
        result = resolution.find_best_resolution(0.5, 1024 / 576, 32)
        self.assert_valid(result, 32)
        self.assertLess(abs(math.log((result[0] / result[1]) / (1024 / 576))), 0.03)

    def test_extreme_target_clamps_to_legal_dimensions(self):
        result = resolution.find_best_resolution(10_000, 21 / 9, 64)
        self.assert_valid(result, 64)

    def test_backend_preserves_valid_authoritative_dimensions(self):
        result = resolution.WN_DragResolution().resolve(1312, 736, "16:9", 32, 1.0)
        self.assertEqual(result[:3], (1312, 736, "41:23"))
        self.assertIn("Target aspect: 16:9", result[3])

    def test_backend_sanitization_uses_selected_aspect(self):
        node = resolution.WN_DragResolution()
        landscape = node.resolve(1024, 1024, "16:9", 32, 1.0)
        free = node.resolve(1001, 997, "Free", 32, 1.0)
        self.assert_valid(landscape[:2], 32)
        self.assert_valid(free[:2], 32)
        self.assertGreater(landscape[0] / landscape[1], 1.6)
        self.assertLess(abs(free[0] / free[1] - 1.0), 0.05)


if __name__ == "__main__":
    unittest.main()
