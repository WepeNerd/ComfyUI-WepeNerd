import math
import unittest

from slider_node import WN_Slider, map_slider_value


class SliderMappingTests(unittest.TestCase):
    def assert_mapping(self, calibration, expected, curve=1.0):
        low, center, high = calibration
        for normalized, strength in expected.items():
            with self.subTest(normalized=normalized):
                self.assertAlmostEqual(
                    map_slider_value(normalized, low, center, high, curve),
                    strength,
                )

    def test_identity_mapping(self):
        self.assert_mapping(
            (-1, 0, 1),
            {-1: -1, -0.5: -0.5, 0: 0, 0.5: 0.5, 1: 1},
        )

    def test_wide_mapping(self):
        self.assert_mapping(
            (-9, 0, 9),
            {-1: -9, -0.5: -4.5, 0: 0, 0.5: 4.5, 1: 9},
        )

    def test_positive_only_mapping_has_no_dead_half(self):
        self.assert_mapping(
            (0, 0.5, 1),
            {-1: 0, -0.5: 0.25, 0: 0.5, 0.5: 0.75, 1: 1},
        )

    def test_offset_positive_mapping(self):
        self.assert_mapping(
            (0.5, 2, 3.5),
            {-1: 0.5, -0.5: 1.25, 0: 2, 0.5: 2.75, 1: 3.5},
        )

    def test_asymmetric_mapping(self):
        self.assert_mapping(
            (-2, 0.5, 6),
            {-1: -2, -0.5: -0.75, 0: 0.5, 0.5: 3.25, 1: 6},
        )

    def test_reversed_mapping_is_not_reordered(self):
        self.assert_mapping((4, 2, 0), {-1: 4, 0: 2, 1: 0})

    def test_curve_and_clamping(self):
        self.assertAlmostEqual(map_slider_value(0.5, -1, 0, 1, 2), 0.25)
        self.assertAlmostEqual(map_slider_value(-2, -3, 0, 3), -3)
        self.assertAlmostEqual(map_slider_value(2, -3, 0, 3), 3)

    def test_invalid_curve_and_nonfinite_values_are_rejected(self):
        with self.assertRaises(ValueError):
            map_slider_value(0, -1, 0, 1, 0)
        with self.assertRaises(ValueError):
            map_slider_value(math.nan, -1, 0, 1)

    def test_node_uses_manual_calibration(self):
        result = WN_Slider().map_value(0, "Realism", 0, 1.5, 3, 1)
        self.assertEqual(result, (1.5,))

    def test_node_has_no_preset_input(self):
        self.assertNotIn("preset", WN_Slider.INPUT_TYPES()["required"])


if __name__ == "__main__":
    unittest.main()
