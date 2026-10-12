import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import frame_number_node as fn  # noqa: E402

H3 = "MiniMax H3 (17n+5, 24 fps)"
LTX2 = "LTX-2.x (8n+1, 24 fps)"


class FrameNumberPresetTest(unittest.TestCase):
    def setUp(self):
        self.presets = fn.load_presets()

    def test_presets_follow_their_frame_grids(self):
        grids = {LTX2: (8, 1), "LTX-Video 0.9.x (8n+1, 25 fps)": (8, 1), H3: (17, 5),
                 "Wan 2.1 / 2.2 14B (4n+1, 16 fps)": (4, 1), "HunyuanVideo (4n+1, 24 fps)": (4, 1),
                 "Mochi 1 (6n+1, 30 fps)": (6, 1)}
        for name, (step, offset) in grids.items():
            frames = self.presets[name]["frames"]
            self.assertTrue(all((f - offset) % step == 0 for f in frames), name)
        self.assertEqual(self.presets[H3]["frames"][-1], 362)
        self.assertIn(124, self.presets[H3]["frames"])

    def test_every_preset_label_passes_combo_validation(self):
        spec = fn.WN_FrameNumberSelector.INPUT_TYPES()["required"]
        allowed = spec["frames"][0]
        self.assertIn(spec["frames"][1]["default"], allowed)
        for name, preset in self.presets.items():
            self.assertIn(preset["default"], preset["labels"], name)
            self.assertTrue(set(preset["labels"]) <= set(allowed), name)

    def test_outputs_int_frames_fps_and_seconds(self):
        frames, fps, seconds = fn.WN_FrameNumberSelector().select(H3, "192  (8.00s)")
        self.assertIsInstance(frames, int)
        self.assertEqual((frames, fps, seconds), (192, 24.0, 8.0))

    def test_illegal_counts_snap_to_nearest_with_ties_going_up(self):
        with self.assertLogs("WepeNerd", level="WARNING"):
            self.assertEqual(fn.select_frames(self.presets, H3, "121  (5.04s)")[0], 124)
        with self.assertLogs("WepeNerd", level="WARNING"):
            self.assertEqual(fn.select_frames(self.presets, LTX2, 13)[0], 17)

    def test_unknown_preset_is_an_error(self):
        with self.assertRaises(ValueError):
            fn.select_frames(self.presets, "Nope", 9)

    def test_explicit_lists_and_bad_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({
                "_readme": "ignored",
                "List": {"frames": [49, 9, 49], "fps": 8, "default": 40},
                "Broken": {"step": 0, "max": 10},
            }))
            with self.assertLogs("WepeNerd", level="WARNING"):
                presets = fn.load_presets(path)
        self.assertEqual(list(presets), ["List"])
        self.assertEqual(presets["List"]["frames"], [9, 49])
        self.assertEqual(presets["List"]["default"], "49  (6.12s)")

    def test_label_parity_with_frontend(self):
        # tests/frame_number.test.mjs reads frame counts from these same labels.
        self.assertEqual(fn.make_label(121, 24), "121  (5.04s)")
        self.assertEqual(fn.parse_frames("362  (15.08s)"), 362)


if __name__ == "__main__":
    unittest.main()
