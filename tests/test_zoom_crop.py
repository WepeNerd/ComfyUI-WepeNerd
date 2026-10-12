import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zoom_crop_node import POSITIONS, WN_ZoomCrop


class ZoomCropTest(unittest.TestCase):
    def setUp(self):
        self.node = WN_ZoomCrop()
        self.image = torch.arange(2 * 12 * 16 * 4, dtype=torch.float64).reshape(2, 12, 16, 4)

    def test_all_positions_preserve_exact_batch_pixels(self):
        self.assertEqual(len(POSITIONS), 25)
        for index, position in enumerate(POSITIONS):
            with self.subTest(position=position):
                result, = self.node.crop(self.image, 8, 8, position)
                x, y = (index % 5) * 2, (index // 5) * 2
                self.assertTrue(torch.equal(result, self.image[:, y:y + 4, x:x + 8, :]))
                self.assertEqual(result.dtype, self.image.dtype)
                self.assertEqual(result.device, self.image.device)
                self.assertNotEqual(result.data_ptr(), self.image.data_ptr())

    def test_named_edges_and_center(self):
        expected = {"left": (0, 4), "right": (8, 4), "top": (4, 0),
                    "bottom": (4, 8), "center": (4, 4)}
        for position, (x, y) in expected.items():
            result, = self.node.crop(self.image, 8, 8, position)
            self.assertTrue(torch.equal(result, self.image[:, y:y + 4, x:x + 8, :]))

    def test_zero_is_noop_and_single_axis_is_supported(self):
        result, = self.node.crop(self.image, 0, 0, "center")
        self.assertIs(result, self.image)
        result, = self.node.crop(self.image, 4, 0, "right")
        self.assertTrue(torch.equal(result, self.image[:, :, 4:, :]))
        result, = self.node.crop(self.image, 0, 4, "bottom")
        self.assertTrue(torch.equal(result, self.image[:, 4:, :, :]))

    def test_odd_amounts_round_offsets_down(self):
        result, = self.node.crop(self.image, 5, 3, "center")
        self.assertEqual(tuple(result.shape), (2, 9, 11, 4))
        self.assertTrue(torch.equal(result, self.image[:, 1:10, 2:13, :]))

    def test_excessive_amounts_retain_one_pixel(self):
        result, = self.node.crop(self.image, 100, 100, "bottom right")
        self.assertTrue(torch.equal(result, self.image[:, -1:, -1:, :]))
        result, = self.node.crop(self.image[:, :1, :1, :], 10, 10, "center")
        self.assertEqual(tuple(result.shape), (2, 1, 1, 4))

    def test_output_does_not_modify_input(self):
        before = self.image.clone()
        result, = self.node.crop(self.image, 8, 8, "top left")
        result.zero_()
        self.assertTrue(torch.equal(self.image, before))


if __name__ == "__main__":
    unittest.main()
