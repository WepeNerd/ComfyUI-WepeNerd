"""Exercise frame selection against real, lossless video files."""

from fractions import Fraction
import importlib.util
import json
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
sys.path.insert(0, str(ROOT))  # load_video_node imports the shared presets from frame_number_node.
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


def write_video(path, count=30, rate=Fraction(30), width=34, height=18, timestamps=None,
                audio=False, audio_delay=0):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("ffv1", rate=rate)
        stream.width, stream.height = width, height
        stream.pix_fmt = "bgr0"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        if audio:
            audio_stream = container.add_stream("pcm_f32le", rate=8000)
            audio_stream.layout = "stereo"
        for index in range(count):
            pixels = np.full((height, width, 3), (index, 80, 200), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = timestamps[index] if timestamps else round(index * 1000 / rate)
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        if audio:
            samples = round(count / rate * 8000)
            origin = round((timestamps[0] / 1000 if timestamps else 0) * 8000)
            for offset in range(0, samples, 800):
                values = np.arange(offset, min(samples, offset + 800), dtype=np.float32) / samples
                frame = av.AudioFrame.from_ndarray(np.stack((values, -values)), format="fltp", layout="stereo")
                frame.sample_rate = 8000
                frame.time_base = Fraction(1, 8000)
                frame.pts = origin + round(audio_delay * 8000) + offset
                for packet in audio_stream.encode(frame):
                    container.mux(packet)
            for packet in audio_stream.encode():
                container.mux(packet)


def write_gop_mp4(path, count=90, rate=30):
    """MPEG-4 with B-frames and a keyframe every 24 frames, so seeking lands mid-file."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width, stream.height = 64, 32
        stream.pix_fmt = "yuv420p"
        stream.codec_context.max_b_frames = 2
        stream.codec_context.gop_size = 24
        stream.codec_context.options = {"qscale": "2"}
        for index in range(count):
            pixels = np.full((32, 64, 3), (index * 2) % 256, dtype=np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def write_alpha_video(path, count=6):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("ffv1", rate=10)
        stream.width, stream.height, stream.pix_fmt = 8, 4, "bgra"
        for index in range(count):
            pixels = np.zeros((4, 8, 4), dtype=np.uint8)
            pixels[..., 0] = index * 10
            pixels[..., 3] = 255
            pixels[:, 4:, 3] = 51  # Right half 80% transparent.
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgba")):
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
        """images, frame_count, frame_rate, audio: the outputs most tests check."""
        return self.load_all(**kwargs)[:4]

    def load_all(self, **kwargs):
        return self.node.load_video("clip.mkv", **kwargs)

    def assert_frames(self, images, expected):
        self.assertEqual(images.dtype, torch.float32)
        self.assertEqual(images.device.type, "cpu")
        self.assertTrue(images.is_contiguous())
        self.assertEqual((images[:, 0, 0, 0] * 255).round().int().tolist(), expected)
        torch.testing.assert_close(images[:, 0, 0, 1], torch.full((len(expected),), 80 / 255))
        torch.testing.assert_close(images[:, 0, 0, 2], torch.full((len(expected),), 200 / 255))

    def test_native_frames_and_rgb(self):
        images, count, rate, _audio = self.load()
        self.assertEqual(tuple(images.shape), (30, 18, 34, 3))
        self.assertEqual(count, 30)
        self.assertEqual(rate, 30)
        self.assert_frames(images, list(range(30)))

    def test_skip_nth_cap(self):
        images, count, rate, _audio = self.load(skip_first_frames=4, select_every_nth=3, frame_load_cap=5)
        self.assert_frames(images, [4, 7, 10, 13, 16])
        self.assertEqual((count, rate), (5, 10))

    def test_rate_conversion_then_skip_nth_cap(self):
        images, count, rate, _audio = self.load(frame_rate=10, skip_first_frames=2,
                                       select_every_nth=2, frame_load_cap=3)
        self.assert_frames(images, [7, 13, 19])
        self.assertEqual((count, rate), (3, 5))

    def test_upsampling_duplicates_frames(self):
        write_video(self.path, count=4, rate=Fraction(10))
        images, count, rate, _audio = self.load(frame_rate=20)
        self.assert_frames(images, [0, 0, 1, 1, 2, 2, 3, 3])
        self.assertEqual((count, rate), (8, 20))

    def test_fractional_rate_and_nonzero_origin(self):
        write_video(self.path, count=10, rate=Fraction(10), timestamps=[5000 + i * 100 for i in range(10)])
        images, count, rate, _audio = self.load(frame_rate=2.5)
        self.assert_frames(images, [1, 5, 9])
        self.assertEqual((count, rate), (3, 2.5))

    def test_same_rate_does_not_drop_frames_with_rounded_timestamps(self):
        images, count, rate, _audio = self.load(frame_rate=30)
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
        native, count, _, _audio = self.node.load_video(path.name)[:4]
        self.assertEqual(count, 30)
        np.testing.assert_allclose(native[:, 0, 0, 0].numpy() * 255, np.arange(30) * 6, atol=6)
        sampled, count, _, _audio = self.node.load_video(path.name, frame_rate=30,
                                                         skip_first_frames=3, select_every_nth=4)[:4]
        torch.testing.assert_close(sampled, native[3::4])
        self.assertEqual(count, len(native[3::4]))

    def test_variable_rate_uses_presentation_timestamps(self):
        write_video(self.path, count=4, rate=Fraction(10), timestamps=[0, 100, 400, 700])
        native, _, _, _audio = self.load()
        self.assert_frames(native, [0, 1, 2, 3])
        images, _, _, _audio = self.load(frame_rate=10, frame_load_cap=8)
        self.assert_frames(images, [0, 1, 1, 1, 2, 2, 2, 3])

    def test_presets(self):
        # Presets only trim the frame count; old short names still work in saved workflows.
        expected = {
            "None": 30, "AnimateDiff": 30, "Mochi": 25, "LTXV": 25, "Hunyuan": 29, "Cosmos": 25,
            "Wan": 29, "H3": 22, "LTX-2.x (8n+1, 24 fps)": 25, "MiniMax H3 (17n+5, 24 fps)": 22,
            "Wan 2.2 5B (4n+1, 24 fps)": 29, "Mochi 1 (6n+1, 30 fps)": 25,
        }
        for preset, count in expected.items():
            with self.subTest(preset=preset):
                images, frames, _, _audio = self.load(format=preset)
                self.assertEqual(tuple(images.shape), (count, 18, 34, 3))
                self.assertEqual(frames, count)
                self.assertIs(self.node.VALIDATE_INPUTS("clip.mkv", preset), True)
        self.assertIsInstance(self.node.VALIDATE_INPUTS("clip.mkv", "Unknown"), str)

    def test_format_list_comes_from_the_shared_presets(self):
        options, config = self.node.INPUT_TYPES()["optional"]["format"]
        self.assertEqual(options, ["None", *video.load_presets()])
        self.assertEqual(config["default"], "None")

    def test_cap_never_exceeded_by_preset(self):
        images, count, _, _audio = self.load(format="Wan", frame_load_cap=8)
        self.assertEqual(count, 5)
        self.assert_frames(images, [0, 1, 2, 3, 4])
        with self.assertRaisesRegex(ValueError, "Increase the cap"):
            self.load(format="H3", frame_load_cap=4)

    def test_short_video_and_large_cap(self):
        write_video(self.path, count=3)
        images, count, _, _audio = self.load(frame_load_cap=100)
        self.assert_frames(images, [0, 1, 2])
        self.assertEqual(count, 3)
        with self.assertRaisesRegex(ValueError, "Too few selected frames"):
            self.load(format="H3")

    def test_skip_to_end_is_clear_error(self):
        for rate in (0, 24):
            with self.subTest(rate=rate), self.assertRaisesRegex(ValueError, "No frames selected"):
                self.load(frame_rate=rate, skip_first_frames=100)

    def test_resizing_is_left_to_other_nodes(self):
        inputs = self.node.INPUT_TYPES()
        self.assertEqual(list(inputs["required"]), ["video"])
        self.assertEqual(list(inputs["optional"]), [
            "start_time", "duration", "frame_load_cap", "frame_rate", "skip_first_frames",
            "select_every_nth", "format", "load_audio"])
        self.assertEqual(inputs["optional"]["frame_load_cap"][1]["default"], 121)

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
        video.probe_video(self.path)  # The node reads (and caches) the file's facts once for its progress bar.
        interrupt.reset_mock()
        images, count, _, _audio = self.load(frame_load_cap=2)
        self.assert_frames(images, [0, 1])
        self.assertEqual(count, 2)
        self.assertEqual(interrupt.call_count, 4)

    def test_invalid_settings(self):
        for settings in ({"frame_rate": float("nan")}, {"frame_rate": -1}, {"start_time": -1},
                         {"duration": float("inf")}, {"frame_load_cap": -1}, {"select_every_nth": 0},
                         {"format": "unknown"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.load(**settings)

    def test_audio_output_is_appended_and_missing_track_is_none(self):
        self.assertEqual(self.node.RETURN_TYPES[:4], ("IMAGE", "INT", "FLOAT", "AUDIO"))
        self.assertIsNone(self.load()[3])

    def test_audio_can_be_disabled_without_decoding(self):
        write_video(self.path, audio=True)
        with patch.object(video, "load_audio_segment") as decode:
            self.assertIsNone(self.load(load_audio=False)[3])
        decode.assert_not_called()

    def test_audio_matches_skip_stride_and_cap(self):
        write_video(self.path, count=10, rate=Fraction(10), audio=True)
        _, count, rate, audio = self.load(frame_rate=10, skip_first_frames=2,
                                         select_every_nth=2, frame_load_cap=3)
        self.assertEqual((count, rate), (3, 5))
        self.assertEqual(audio["sample_rate"], 8000)
        self.assertEqual(tuple(audio["waveform"].shape), (1, 2, 4800))
        expected = np.arange(1600, 6400, dtype=np.float32) / 8000
        np.testing.assert_allclose(audio["waveform"][0, 0].numpy(), expected)
        np.testing.assert_allclose(audio["waveform"][0, 1].numpy(), -expected)

    def test_audio_uses_final_preset_trimmed_count(self):
        write_video(self.path, count=10, rate=Fraction(10), audio=True)
        _, count, _, audio = self.load(format="Wan", frame_load_cap=8)
        self.assertEqual(count, 5)
        self.assertEqual(audio["waveform"].shape[-1], 4000)

    def test_audio_respects_video_timestamp_origin(self):
        write_video(self.path, count=10, rate=Fraction(10), audio=True,
                    timestamps=[5000 + i * 100 for i in range(10)])
        for frame_rate in (0, 10):
            with self.subTest(frame_rate=frame_rate):
                _, _, _, audio = self.load(frame_rate=frame_rate, skip_first_frames=2, frame_load_cap=3)
                expected = np.arange(1600, 4000, dtype=np.float32) / 8000
                np.testing.assert_allclose(audio["waveform"][0, 0].numpy(), expected)

    def test_audio_delay_is_preserved_with_silence(self):
        write_video(self.path, count=10, rate=Fraction(10), audio=True, audio_delay=0.25)
        audio = self.load(frame_load_cap=5)[3]
        self.assertEqual(tuple(audio["waveform"].shape), (1, 2, 4000))
        self.assertFalse(audio["waveform"][:, :, :2000].any())
        np.testing.assert_allclose(audio["waveform"][0, 0, 2000:].numpy(),
                                   np.arange(2000, dtype=np.float32) / 8000)


    def test_start_time_and_duration(self):
        images, count, rate, _audio = self.load(start_time=0.5, duration=0.3)
        self.assert_frames(images, [15, 16, 17, 18, 19, 20, 21, 22, 23])
        self.assertEqual((count, rate), (9, 30))
        # Off-grid times pick the nearest source frames.
        images, _, _, _audio = self.load(start_time=0.51, duration=0.1)
        self.assert_frames(images, [15, 16, 17])
        # Resampling starts its grid at start_time; skip, nth and cap apply inside the window.
        images, count, rate, _audio = self.load(frame_rate=10, start_time=0.2, duration=0.5,
                                               skip_first_frames=1, select_every_nth=2)
        self.assert_frames(images, [10, 16])  # The same grid as from the top: rate 10 picks frames 1, 4, 7, 10, ...
        self.assertEqual((count, rate), (2, 5))
        with self.assertRaisesRegex(ValueError, "No frames selected"):
            self.load(start_time=5)

    def test_seeking_matches_decoding_from_the_top(self):
        path = self.root / "gop.mp4"
        write_gop_mp4(path)

        def refuse(*args, **kwargs):
            raise av.error.FFmpegError(1, "seek refused")

        for settings in ({"start_time": 1.5}, {"start_time": 2.0, "duration": 0.5},
                         {"start_time": 1.234, "frame_rate": 24}, {"start_time": 0.9, "frame_rate": 12.5},
                         {"start_time": 2.5, "skip_first_frames": 2, "select_every_nth": 2}):
            with self.subTest(settings=settings):
                seeked = self.node.load_video(path.name, frame_load_cap=0, **settings)[0]
                with patch.object(video, "seek", refuse):
                    decoded = self.node.load_video(path.name, frame_load_cap=0, **settings)[0]
                torch.testing.assert_close(seeked, decoded, rtol=0, atol=0)

    def test_inexact_seek_falls_back_to_the_start(self):
        path = self.root / "gop.mp4"
        write_gop_mp4(path)
        expected = self.node.load_video(path.name, start_time=2.0, frame_load_cap=5)[0]
        real_seek = video.seek
        calls = []

        def overshoot(container, stream, decoder, seconds):
            calls.append(seconds)
            real_seek(container, stream, decoder, seconds + 1 if len(calls) == 1 else seconds)

        with patch.object(video, "seek", overshoot):
            images = self.node.load_video(path.name, start_time=2.0, frame_load_cap=5)[0]
        self.assertEqual(len(calls), 2)
        torch.testing.assert_close(images, expected, rtol=0, atol=0)

    def test_audio_follows_start_time(self):
        write_video(self.path, count=10, rate=Fraction(10), audio=True)
        for frame_rate in (0, 10):
            with self.subTest(frame_rate=frame_rate):
                audio = self.load(frame_rate=frame_rate, start_time=0.3, duration=0.4)[3]
                expected = np.arange(2400, 5600, dtype=np.float32) / 8000
                np.testing.assert_allclose(audio["waveform"][0, 0].numpy(), expected)

    def test_outputs_and_empty_mask(self):
        self.assertEqual(self.node.RETURN_NAMES, ("images", "frame_count", "frame_rate", "audio", "mask"))
        images, count, rate, _, mask = self.load_all(select_every_nth=2)
        self.assertEqual((count, rate, tuple(images.shape[1:3])), (15, 15, (18, 34)))
        self.assertEqual(tuple(mask.shape), (1, 64, 64))
        self.assertFalse(mask.any())

    def test_transparency_becomes_a_mask(self):
        write_alpha_video(self.root / "alpha.mkv")
        images, count, _, _, mask, *_ = self.node.load_video("alpha.mkv", select_every_nth=2)
        self.assertEqual((count, tuple(images.shape), tuple(mask.shape)), (3, (3, 4, 8, 3), (3, 4, 8)))
        self.assertTrue(mask.is_contiguous())
        torch.testing.assert_close(mask[:, :, :4], torch.zeros(3, 4, 4))
        torch.testing.assert_close(mask[:, :, 4:], torch.full((3, 4, 4), 0.8))
        self.assertEqual((images[:, 0, 0, 0] * 255).round().int().tolist(), [0, 20, 40])
        self.assertTrue(video.probe_video(self.root / "alpha.mkv")["alpha"])

    @unittest.skipUnless("libvpx-vp9" in av.codecs_available, "PyAV was built without libvpx")
    def test_vp9_transparency_uses_libvpx(self):
        path = self.root / "alpha.webm"
        with av.open(str(path), "w") as container:
            stream = container.add_stream("libvpx-vp9", rate=10)
            stream.width, stream.height, stream.pix_fmt = 16, 16, "yuva420p"
            stream.options = {"lossless": "1"}
            for _ in range(3):
                pixels = np.zeros((16, 16, 4), dtype=np.uint8)
                pixels[..., 3] = 255
                pixels[:, 8:, 3] = 0
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgba")):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        mask = self.node.load_video("alpha.webm")[4]
        self.assertEqual(tuple(mask.shape), (3, 16, 16))
        self.assertLess(float(mask[:, :, :8].max()), 0.05)
        self.assertGreater(float(mask[:, :, 8:].min()), 0.95)

    def test_probe_reports_source_facts(self):
        write_video(self.path, count=12, rate=Fraction(24), audio=True)
        info = video.probe_video(self.path)
        self.assertEqual({key: info[key] for key in ("width", "height", "rotation", "fps", "frames", "audio", "alpha")},
                         {"width": 34, "height": 18, "rotation": 0, "fps": 24.0, "frames": 12, "audio": True, "alpha": False})
        self.assertAlmostEqual(info["duration"], 0.5, places=2)

    def test_plan_predicts_the_loaded_clip(self):
        info = video.probe_video(self.path)
        for settings in ({}, {"frame_load_cap": 7}, {"start_time": 0.5, "duration": 0.3},
                         {"frame_rate": 10, "start_time": 0.2, "duration": 0.5, "skip_first_frames": 1, "select_every_nth": 2},
                         {"skip_first_frames": 4, "select_every_nth": 3, "frame_load_cap": 5}):
            with self.subTest(settings=settings):
                plan = video.plan_clip(info, **settings)
                _, count, rate, _audio = self.load(**settings)
                self.assertEqual((plan["count"], plan["frame_rate"]), (count, rate))

    def test_plan_matches_the_browser_on_shared_cases(self):
        shared = json.loads((ROOT / "tests" / "load_video_plans.json").read_text(encoding="utf-8"))
        for case in shared["cases"]:
            preset = shared["presets"][case["preset"]] if case["preset"] else None
            if preset is not None:
                preset = {**preset, "frames": preset.get("frames")}
            with self.subTest(case=case):
                plan = video.plan_clip(shared["infos"][case["info"]], preset=preset, **case["settings"])
                for key, value in case["expected"].items():
                    self.assertAlmostEqual(plan[key], value, places=6) if isinstance(value, float) \
                        else self.assertEqual(plan[key], value)

    def test_listing_skips_hidden_folders_and_is_briefly_cached(self):
        hidden = self.root / ".cache"
        hidden.mkdir()
        write_video(hidden / "skip.mkv", count=1)
        self.assertEqual(video.list_videos(self.root), ["clip.mkv"])
        write_video(self.root / "new.mkv", count=1)
        self.assertEqual(video.list_videos(self.root), ["clip.mkv"])
        with patch.object(video, "LIST_CACHE_SECONDS", 0):
            self.assertEqual(video.list_videos(self.root), ["clip.mkv", "new.mkv"])


if __name__ == "__main__":
    unittest.main()
