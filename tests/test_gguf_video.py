import importlib
import io
import types
import unittest

import numpy as np


video_utils = importlib.import_module("wepenerd_testpkg.wn_gguf_video")


class FakeVideo:
    def __init__(self, frame_count=33, fps=24.0, data=b"video"):
        self.frames = np.zeros((frame_count, 8, 16, 3), dtype=np.float32)
        self.fps = fps
        self.data = data

    def get_components(self):
        return types.SimpleNamespace(images=self.frames, frame_rate=self.fps)

    def get_stream_source(self):
        return io.BytesIO(self.data)

    def get_active_trim_window(self):
        return 0.0, 0.0

    def get_frame_rate(self):
        return self.fps

    def get_duration(self):
        return len(self.frames) / self.fps

    def get_frame_count(self):
        return len(self.frames)


class LongFakeVideo(FakeVideo):
    def __init__(self):
        self.fps = 24.0
        self.data = b"not-a-real-stream"
        self.components_called = False

    def get_frame_count(self):
        return video_utils.SAFE_COMPONENT_FRAME_LIMIT + 1

    def get_duration(self):
        return self.get_frame_count() / self.fps

    def get_components(self):
        self.components_called = True
        raise AssertionError("long video must not be materialized")


class VideoSamplingTests(unittest.TestCase):
    def test_uniform_sampling_includes_first_last_and_caps(self):
        for frame_count in (33, 65, 73, 97, 121):
            indices = video_utils.uniform_sample_indices(frame_count, 12, 24)
            self.assertEqual(indices[0], 0)
            self.assertEqual(indices[-1], frame_count - 1)
            self.assertEqual(len(indices), len(set(indices)))
            self.assertLessEqual(len(indices), 24)

    def test_fixed_fps_sampling_includes_end_and_caps(self):
        indices = video_utils.fixed_fps_sample_indices(121, 24.0, 2.0, 8)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 120)
        self.assertLessEqual(len(indices), 8)

    def test_timestamps(self):
        self.assertEqual(video_utils.timestamps_for_indices([0, 12, 24], 24.0), [0.0, 0.5, 1.0])

    def test_sampled_frame_preparation(self):
        result = video_utils.prepare_sampled_frames(
            FakeVideo(33, 24.0), "uniform", 5, 2.0, 8, 1024, 90
        )
        self.assertEqual(len(result["urls"]), 5)
        self.assertEqual(result["indices"][0], 0)
        self.assertEqual(result["indices"][-1], 32)
        self.assertAlmostEqual(result["timestamps"][-1], 32 / 24.0)

    def test_unknown_fps_uses_safe_one_fps_fallback(self):
        result = video_utils.prepare_sampled_frames(
            FakeVideo(5, 0.0), "uniform", 3, 2.0, 8, 1024, 90
        )
        self.assertEqual(result["fps"], 1.0)
        self.assertEqual(result["timestamps"][-1], 4.0)

    def test_native_video_is_base64_and_size_limited(self):
        result = video_utils.prepare_native_video(FakeVideo(data=b"abc"), 1)
        self.assertEqual(result["base64"], "YWJj")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            video_utils.prepare_native_video(FakeVideo(data=b"x" * (1024 * 1024 + 1)), 1)

    def test_long_stream_failure_never_materializes_all_components(self):
        video = LongFakeVideo()
        with self.assertRaisesRegex(ValueError, "Refusing to materialize"):
            video_utils.prepare_sampled_frames(video, "uniform", 12, 2.0, 24, 1024, 90)
        self.assertFalse(video.components_called)

    def test_seekable_source_prefers_seek_extraction(self):
        expected = {"urls": ["data:a"], "timestamps": [0.0]}
        video = FakeVideo()
        with unittest.mock.patch.object(
            video_utils, "_prepare_seek_sampled_frames", return_value=expected
        ) as seek, unittest.mock.patch.object(video, "get_components") as components:
            result = video_utils.prepare_sampled_frames(video, "uniform", 3, 2.0, 8, 1024, 90)
        self.assertIs(result, expected)
        seek.assert_called_once()
        components.assert_not_called()
