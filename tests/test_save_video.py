"""Encode real files with every format and decode them back."""

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
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
sys.path.insert(0, str(ROOT))
original_management = sys.modules.get("comfy.model_management")
sys.modules["comfy.model_management"] = types.SimpleNamespace(throw_exception_if_processing_interrupted=Mock())
try:
    spec = importlib.util.spec_from_file_location("wepenerd_save_video_test", ROOT / "save_video_node.py")
    save = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(save)
    spec = importlib.util.spec_from_file_location("wepenerd_save_video_load", ROOT / "load_video_node.py")
    load = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(load)
finally:
    if original_management is None:
        del sys.modules["comfy.model_management"]
    else:
        sys.modules["comfy.model_management"] = original_management


def gradient(count=12, width=48, height=32, channels=3):
    y, x = torch.meshgrid(torch.linspace(0, 1, height), torch.linspace(0, 1, width), indexing="ij")
    frames = [torch.stack((x, y, torch.full_like(x, index / count), torch.full_like(x, 1.0))[:channels], -1)
              for index in range(count)]
    return torch.stack(frames)


def decode(path, alpha=False):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        decoder = load.alpha_decoder(stream) if alpha else stream.codec_context
        frames = [frame.to_ndarray(format="rgba" if alpha else "rgb24")
                  for frame in load.decoded_frames(container, stream, decoder)]
        context = stream.codec_context
        facts = {"rate": stream.average_rate, "pix_fmt": context.pix_fmt, "colorspace": context.colorspace,
                 "color_trc": context.color_trc,
                 "alpha_mode": {k.lower(): v for k, v in stream.metadata.items()}.get("alpha_mode")}
        return np.stack(frames), facts, dict(container.metadata)


class SaveVideoTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for name in ("get_output_directory", "get_temp_directory"):
            patcher = patch.object(save.folder_paths, name, return_value=self.folder.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        progress = patch.object(save, "ProgressBar", Mock())
        progress.start()
        self.addCleanup(progress.stop)

    def run_node(self, images, format, **kwargs):
        kwargs.setdefault("prompt", {"1": {"class_type": "WN_SaveVideo"}})
        kwargs.setdefault("extra_pnginfo", {"workflow": {"nodes": [1]}})
        result = save.WN_SaveVideo().save_video(images, kwargs.pop("fps", 24.0), "clips/test", format, **kwargs)
        path = Path(result["result"][0])
        self.assertTrue(path.is_file())
        self.assertEqual(result["ui"]["images"][0]["subfolder"], "clips")
        return path

    def assertClose(self, decoded, images, tolerance):
        expected = (images[..., :3].numpy() * 255).round()
        self.assertLess(np.abs(decoded[..., :3].astype(np.float64) - expected).mean(), tolerance)

    def test_h264_roundtrip_with_metadata_and_bt709_tags(self):
        images = gradient()
        path = self.run_node(images, "MP4 · H.264", crf=10)
        self.assertEqual(path.name, "test_00001_.mp4")
        frames, stream, metadata = decode(path)
        self.assertEqual(frames.shape, (12, 32, 48, 3))
        self.assertClose(frames, images, 3)
        self.assertEqual(stream["rate"], 24)
        self.assertEqual(json.loads(metadata["workflow"]), {"nodes": [1]})
        self.assertIn("prompt", metadata)
        self.assertEqual((stream["colorspace"], stream["color_trc"]), (1, 1))
        # A second save takes the next number instead of overwriting.
        self.assertEqual(self.run_node(images, "MP4 · H.264").name, "test_00002_.mp4")

    def test_odd_size_is_padded_for_420(self):
        images = gradient(width=33, height=17)
        frames, *_ = decode(self.run_node(images, "MP4 · H.264", crf=0))
        self.assertEqual(frames.shape[1:3], (18, 34))
        self.assertClose(frames[:, :17, :33], images, 5)  # 4:2:0 chroma blurs a steep 17-pixel gradient.
        np.testing.assert_allclose(frames[:, :17, 33].astype(float), frames[:, :17, 32].astype(float), atol=4)

    def test_high_bit_depth_formats(self):
        images = gradient(width=64, height=32)
        for format in ("MP4 · H.265 10-bit", "MP4 · AV1 10-bit"):
            if format not in save.available_formats():
                continue
            with self.subTest(format=format):
                frames, stream, _ = decode(self.run_node(images, format, crf=12))
                self.assertEqual(stream["pix_fmt"], "yuv420p10le")
                self.assertClose(frames, images, 3)

    def test_ffv1_is_lossless_at_16_bits(self):
        images = gradient(count=4)
        path = self.run_node(images, "MKV · FFV1 16-bit lossless")
        with av.open(str(path)) as container:
            frames = np.stack([frame.to_ndarray(format="rgb48le") for frame in container.decode(video=0)])
        expected = (images.numpy() * 65535).round()
        self.assertEqual(np.abs(frames.astype(np.float64) - expected).max(), 0)

    def test_transparency_from_mask_in_vp9_prores_and_ffv1(self):
        images = gradient(count=6)
        mask = torch.zeros((1, 32, 48))
        mask[:, :, :24] = 1  # Left half transparent, for every frame.
        for format, pix_fmt in (("WebM · VP9", None), ("MOV · ProRes (4444 with alpha)", "yuva444p12le"),
                                ("MKV · FFV1 16-bit lossless", "gbrap16le")):
            with self.subTest(format=format):
                frames, stream, _ = decode(self.run_node(images, format, mask=mask), alpha=True)
                if pix_fmt is None:
                    self.assertEqual(stream["alpha_mode"], "1")
                else:
                    self.assertEqual(stream["pix_fmt"], pix_fmt)  # FFmpeg decodes ProRes 4444 at 12 bits.
                self.assertLess(frames[:, :, :20, 3].max(), 10)
                self.assertGreater(frames[:, :, 28:, 3].min(), 245)

    def test_rgba_images_and_placeholder_masks(self):
        images = gradient(count=3, channels=4)
        images[:, :8, :, 3] = 0
        frames, *_ = decode(self.run_node(images, "WebM · VP9"), alpha=True)
        self.assertLess(frames[:, :6, :, 3].max(), 10)
        self.assertIsNone(save.alpha_from(gradient(), torch.zeros((1, 64, 64))))
        with self.assertRaises(ValueError):
            save.alpha_from(gradient(), torch.ones((1, 64, 64)))
        with self.assertRaises(ValueError):
            save.alpha_from(gradient(count=3), torch.ones((2, 32, 48)))

    def test_alpha_is_dropped_for_mp4(self):
        mask = torch.ones((1, 32, 48))
        frames, *_ = decode(self.run_node(gradient(count=2), "MP4 · H.264", mask=mask, crf=0))
        self.assertClose(frames, gradient(count=2), 3)

    def test_audio_is_muxed_once_and_trimmed(self):
        images = gradient(count=24)
        samples = torch.linspace(-0.5, 0.5, 3 * 44100).repeat(2, 1).unsqueeze(0)
        audio = {"waveform": samples, "sample_rate": 44100}
        for format, codec, rate in (("MP4 · H.264", "aac", 44100), ("WebM · VP9", "opus", 48000),
                                    ("MOV · ProRes (4444 with alpha)", "pcm_s16le", 44100),
                                    ("MKV · FFV1 16-bit lossless", "flac", 44100)):
            with self.subTest(format=format):
                path = self.run_node(images, format, audio=audio)
                self.assertEqual(len(list(Path(self.folder.name, "clips").glob(path.stem + "*"))), 1)
                with av.open(str(path)) as container:
                    stream = container.streams.audio[0]
                    self.assertEqual((stream.codec_context.name, stream.rate, stream.channels), (codec, rate, 2))
                    seconds = sum(frame.samples for frame in container.decode(audio=0)) / rate
                # One second of video; encoders add up to one frame of priming and padding.
                self.assertAlmostEqual(seconds, 1.0, delta=0.05)

    def test_pingpong(self):
        self.assertEqual(save.frame_order(4, True), [0, 1, 2, 3, 2, 1])
        self.assertEqual(save.frame_order(1, True), [0])
        images = gradient(count=4)
        frames, *_ = decode(self.run_node(images, "MKV · FFV1 16-bit lossless", pingpong=True))
        self.assertEqual(len(frames), 6)
        np.testing.assert_array_equal(frames[1], frames[5])

    def test_gif_with_transparency(self):
        images = gradient(count=5)
        mask = torch.zeros((5, 32, 48))
        mask[::2, :10] = 1
        path = self.run_node(images, "GIF", mask=mask, fps=10)
        with Image.open(path) as gif:
            self.assertEqual(gif.n_frames, 5)
            self.assertEqual(gif.info.get("loop"), 0)
            self.assertEqual(gif.info["duration"], 100)
        frames, *_ = decode(path, alpha=True)
        self.assertEqual(frames[0, :8, :, 3].max(), 0)
        self.assertEqual(frames[1, :8, :, 3].min(), 255)

    def test_webp_lossless_with_workflow(self):
        images = gradient(count=3)
        path = self.run_node(images, "WebP", crf=0)
        with Image.open(path) as webp:
            self.assertEqual(webp.n_frames, 3)
            exif = webp.getexif()
            self.assertTrue(exif[0x010F].startswith("workflow:"))
            webp.seek(2)
            pixels = np.asarray(webp.convert("RGB"))
        np.testing.assert_array_equal(pixels, (images[2].numpy() * 255).round().astype(np.uint8))

    def test_ntsc_rates(self):
        self.assertEqual(save.frame_rate(23.976), Fraction(24000, 1001))
        self.assertEqual(save.frame_rate(29.97), Fraction(30000, 1001))
        self.assertEqual(save.frame_rate(24.0), 24)
        self.assertEqual(save.frame_rate(12.5), Fraction(25, 2))
        frames, stream, _ = decode(self.run_node(gradient(count=3), "MP4 · H.264", fps=23.976))
        self.assertEqual(stream["rate"], Fraction(24000, 1001))

    def test_failed_encode_leaves_no_file(self):
        with patch.object(save, "encode_video", side_effect=RuntimeError("boom")) as encode:
            def write_then_fail(path, *args):
                Path(path).write_bytes(b"partial")
                raise RuntimeError("boom")
            encode.side_effect = write_then_fail
            with self.assertRaises(RuntimeError):
                save.WN_SaveVideo().save_video(gradient(count=2), 24.0, "broken", "MP4 · H.264")
        self.assertEqual(list(Path(self.folder.name).glob("broken*")), [])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            save.WN_SaveVideo().save_video(gradient(), 0.0, "x", "MP4 · H.264")
        with self.assertRaises(ValueError):
            save.WN_SaveVideo().save_video(torch.zeros((0, 8, 8, 3)), 24.0, "x", "MP4 · H.264")


if __name__ == "__main__":
    unittest.main()
