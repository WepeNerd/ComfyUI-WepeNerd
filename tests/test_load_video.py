"""Exercise frame selection against real, lossless video files."""

from fractions import Fraction
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import av
import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
spec = importlib.util.spec_from_file_location("wepenerd_video_test", ROOT / "load_video_node.py")
video = importlib.util.module_from_spec(spec)
# The decoder runs on CPU; importing host GPU initialization is unnecessary here.
interrupt = Mock()
original_management = sys.modules.get("comfy.model_management")
sys.modules["comfy.model_management"] = types.SimpleNamespace(
    throw_exception_if_processing_interrupted=interrupt)
try:
    spec.loader.exec_module(video)
finally:
    if original_management is None:
        del sys.modules["comfy.model_management"]
    else:
        sys.modules["comfy.model_management"] = original_management


def write_video(path, count=30, rate=Fraction(30), width=34, height=18, timestamps=None):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("ffv1", rate=rate)
        stream.width, stream.height = width, height
        stream.pix_fmt = "bgr0"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        for index in range(count):
            pixels = np.full((height, width, 3), (index, 80, 200), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = timestamps[index] if timestamps else round(index * 1000 / rate)
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class LoadVideoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "clip.mkv"
        write_video(self.path)
        self.input_patch = patch.object(video.folder_paths, "get_input_directory", return_value=str(self.root))
        self.input_patch.start()
        self.addCleanup(self.input_patch.stop)
        interrupt.reset_mock(side_effect=True)
        self.node = video.WN_LoadVideo()

    def load(self, **kwargs):
        return self.node.load_video("clip.mkv", **kwargs)

    def assert_frames(self, images, expected):
        self.assertEqual(images.dtype, torch.float32)
        self.assertEqual(images.device.type, "cpu")
        self.assertTrue(images.is_contiguous())
        self.assertEqual((images[:, 0, 0, 0] * 255).round().int().tolist(), expected)
        torch.testing.assert_close(images[:, 0, 0, 1], torch.full((len(expected),), 80 / 255))
        torch.testing.assert_close(images[:, 0, 0, 2], torch.full((len(expected),), 200 / 255))

    def test_native_frames_and_rgb(self):
        images, count, rate = self.load()
        self.assertEqual(tuple(images.shape), (30, 18, 34, 3))
        self.assertEqual(count, 30)
        self.assertEqual(rate, 30)
        self.assert_frames(images, list(range(30)))

    def test_skip_nth_cap(self):
        images, count, rate = self.load(skip_first_frames=4, select_every_nth=3, frame_load_cap=5)
        self.assert_frames(images, [4, 7, 10, 13, 16])
        self.assertEqual((count, rate), (5, 10))

    def test_rate_conversion_then_skip_nth_cap(self):
        images, count, rate = self.load(frame_rate=10, skip_first_frames=2,
                                       select_every_nth=2, frame_load_cap=3)
        self.assert_frames(images, [7, 13, 19])
        self.assertEqual((count, rate), (3, 5))

    def test_upsampling_duplicates_frames(self):
        write_video(self.path, count=4, rate=Fraction(10))
        images, count, rate = self.load(frame_rate=20)
        self.assert_frames(images, [0, 0, 1, 1, 2, 2, 3, 3])
        self.assertEqual((count, rate), (8, 20))

    def test_fractional_rate_and_nonzero_origin(self):
        write_video(self.path, count=10, rate=Fraction(10), timestamps=[5000 + i * 100 for i in range(10)])
        images, count, rate = self.load(frame_rate=2.5)
        self.assert_frames(images, [1, 5, 9])
        self.assertEqual((count, rate), (3, 2.5))

    def test_same_rate_does_not_drop_frames_with_rounded_timestamps(self):
        images, count, rate = self.load(frame_rate=30)
        self.assert_frames(images, list(range(30)))
        self.assertEqual((count, rate), (30, 30))

    def test_mp4_with_b_frames_flushes_and_preserves_selection(self):
        path = self.root / "reordered.mp4"
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=30)
            stream.width, stream.height = 64, 32
            stream.pix_fmt = "yuv420p"
            stream.codec_context.max_b_frames = 2
            for index in range(30):
                pixels = np.full((32, 64, 3), index * 6, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        native, count, _ = self.node.load_video(path.name)
        self.assertEqual(count, 30)
        np.testing.assert_allclose(native[:, 0, 0, 0].numpy() * 255, np.arange(30) * 6, atol=6)
        sampled, count, _ = self.node.load_video(path.name, frame_rate=30,
                                               skip_first_frames=3, select_every_nth=4)
        torch.testing.assert_close(sampled, native[3::4])
        self.assertEqual(count, len(native[3::4]))

    def test_variable_rate_uses_presentation_timestamps(self):
        write_video(self.path, count=4, rate=Fraction(10), timestamps=[0, 100, 400, 700])
        native, _, _ = self.load()
        self.assert_frames(native, [0, 1, 2, 3])
        images, _, _ = self.load(frame_rate=10, frame_load_cap=8)
        self.assert_frames(images, [0, 1, 1, 1, 2, 2, 2, 3])

    def test_presets(self):
        expected = {
            "None": (30, 18, 34), "AnimateDiff": (30, 16, 32),
            "Mochi": (25, 16, 32), "LTXV": (25, 32, 32),
            "Hunyuan": (29, 16, 32), "Cosmos": (25, 16, 32),
            "Wan": (29, 16, 32), "H3": (22, 32, 32),
        }
        for preset, shape in expected.items():
            with self.subTest(preset=preset):
                images, count, _ = self.load(format=preset)
                self.assertEqual(tuple(images.shape), (*shape, 3))
                self.assertEqual(count, shape[0])

    def test_cap_never_exceeded_by_preset(self):
        images, count, _ = self.load(format="Wan", frame_load_cap=8)
        self.assertEqual(count, 5)
        self.assert_frames(images, [0, 1, 2, 3, 4])
        with self.assertRaisesRegex(ValueError, "Increase the cap"):
            self.load(format="H3", frame_load_cap=4)

    def test_short_video_and_large_cap(self):
        write_video(self.path, count=3)
        images, count, _ = self.load(frame_load_cap=100)
        self.assert_frames(images, [0, 1, 2])
        self.assertEqual(count, 3)
        with self.assertRaisesRegex(ValueError, "Too few selected frames"):
            self.load(format="H3")

    def test_skip_to_end_is_clear_error(self):
        for rate in (0, 24):
            with self.subTest(rate=rate), self.assertRaisesRegex(ValueError, "No frames selected"):
                self.load(frame_rate=rate, skip_first_frames=100)

    def test_custom_dimensions(self):
        images, _, _ = self.load(custom_width=68)
        self.assertEqual(tuple(images.shape[1:]), (36, 68, 3))
        images, _, _ = self.load(custom_height=36)
        self.assertEqual(tuple(images.shape[1:]), (36, 68, 3))
        images, _, _ = self.load(custom_width=40, custom_height=24)
        self.assertEqual(tuple(images.shape[1:]), (24, 40, 3))

    def test_file_listing_and_upload(self):
        nested = self.root / "nested"
        nested.mkdir()
        write_video(nested / "second.MKV", count=1)
        (self.root / "ignore.txt").write_text("not a video")
        options, config = self.node.INPUT_TYPES()["required"]["video"]
        self.assertEqual(options, ["clip.mkv", "nested/second.MKV"])
        self.assertTrue(config["video_upload"])
        self.assertTrue(self.node.VALIDATE_INPUTS("nested/second.MKV [input]"))
        self.assertEqual(self.node.load_video("nested/second.MKV [input]")[1], 1)

    def test_paths_cannot_escape_input(self):
        for path in ("../outside.mp4", str(self.root.parent / "outside.mp4"), "clip.mkv [output]"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.node.load_video(path)
        self.assertIsInstance(self.node.VALIDATE_INPUTS("missing.mp4"), str)

    def test_cache_changes_when_file_is_replaced(self):
        before = self.node.IS_CHANGED("clip.mkv")
        write_video(self.path, count=4)
        self.assertNotEqual(before, self.node.IS_CHANGED("clip.mkv"))

    def test_cancellation_closes_file(self):
        interrupt.side_effect = RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.load()
        # Windows will reject this rename if the decoder still owns an open handle.
        self.path.rename(self.root / "closed.mkv")

    def test_cap_stops_decoding(self):
        images, count, _ = self.load(frame_load_cap=2)
        self.assert_frames(images, [0, 1])
        self.assertEqual(count, 2)
        self.assertEqual(interrupt.call_count, 4)

    def test_invalid_settings(self):
        for settings in ({"frame_rate": float("nan")}, {"frame_rate": -1},
                         {"frame_load_cap": -1}, {"select_every_nth": 0}, {"format": "unknown"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.load(**settings)


if __name__ == "__main__":
    unittest.main()
