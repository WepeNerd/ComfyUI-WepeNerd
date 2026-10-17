import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import frame_slicer_node as fs  # noqa: E402


def batch(count, height=4, width=6):
    """Frame i is filled with the value i, so a slice's frame numbers can be read back."""
    return torch.arange(count, dtype=torch.float32).view(count, 1, 1, 1).expand(count, height, width, 3).clone()


def frame_ids(images):
    return [int(v) for v in images[:, 0, 0, 0]]


class FrameSlicerTest(unittest.TestCase):
    def run_node(self, images, start, stop):
        return fs.WN_FrameSlicer().slice(images, start, stop)[0]

    def test_matches_python_slicing(self):
        images = batch(10)
        for start, stop in [(0, 1), (2, 5), (0, 10), (9, 10), (3, 4)]:
            self.assertEqual(frame_ids(self.run_node(images, start, stop)), list(range(start, stop)))

    def test_stop_past_end_keeps_the_rest(self):
        self.assertEqual(frame_ids(self.run_node(batch(5), 2, 999)), [2, 3, 4])

    def test_output_keeps_shape_dtype_and_is_a_copy(self):
        images = batch(6, height=8, width=12).half()
        out = self.run_node(images, 1, 4)
        self.assertEqual(tuple(out.shape), (3, 8, 12, 3))
        self.assertEqual(out.dtype, torch.float16)
        out.zero_()
        self.assertEqual(frame_ids(images), [0, 1, 2, 3, 4, 5])

    def test_empty_ranges_raise_clear_errors(self):
        images = batch(5)
        with self.assertRaisesRegex(ValueError, "only has 5 frame"):
            self.run_node(images, 5, 8)
        with self.assertRaisesRegex(ValueError, "greater than start"):
            self.run_node(images, 3, 3)
        with self.assertRaisesRegex(ValueError, "greater than start"):
            self.run_node(images, 4, 2)

    def test_inputs_match_res4lyf_frames_slice(self):
        spec = fs.WN_FrameSlicer.INPUT_TYPES()["required"]
        self.assertEqual(list(spec), ["frames", "start", "stop"])
        self.assertEqual(spec["frames"][0], "IMAGE")
        self.assertEqual((spec["start"][1]["default"], spec["start"][1]["min"]), (0, 0))
        self.assertEqual((spec["stop"][1]["default"], spec["stop"][1]["min"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
