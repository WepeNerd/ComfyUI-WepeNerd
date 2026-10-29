"""H3 Ripple: 24 fps grid decoding, windows, markers, conditioning, caching and saving.

Runs on CPU with ComfyUI on PYTHONPATH (for comfy.utils). folder_paths, the H3 latent helper and
node_helpers are replaced by small stand-ins so no model code is imported.
"""

import fractions
import importlib.util
import io
import json
import math
import os
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
sys.path.insert(0, str(ROOT))

folder_paths = types.ModuleType("folder_paths")
folder_paths.ROOT = tempfile.gettempdir()
folder_paths.get_input_directory = lambda: folder_paths.ROOT
folder_paths.get_output_directory = lambda: os.path.join(folder_paths.ROOT, "output")
folder_paths.get_temp_directory = lambda: os.path.join(folder_paths.ROOT, "temp")
folder_paths.get_annotated_filepath = lambda name: os.path.join(folder_paths.ROOT, name)


def _save_path(prefix, directory, width=0, height=0):
    subfolder, name = os.path.split(os.path.normpath(prefix))
    folder = os.path.join(directory, subfolder)
    os.makedirs(folder, exist_ok=True)
    counter = len([f for f in os.listdir(folder) if f.startswith(name + "_")]) + 1
    return folder, name, counter, subfolder, prefix


folder_paths.get_save_image_path = _save_path


def _empty_av_latent(width, height, length):
    frames = length if length % 17 == 5 else length + 17 - (length - 5) % 17
    t = 2 if frames <= 5 else ((frames - 5) // 17) * 5 + 2
    return {"samples": (torch.zeros(1, 24, t, height // 16, width // 16),)}, frames


minimax = types.ModuleType("comfy_extras.nodes_minimax_h3")
minimax._empty_av_latent = _empty_av_latent
node_helpers = types.ModuleType("node_helpers")
node_helpers.conditioning_set_values = lambda cond, values: [[c[0], {**c[1], **values}] for c in cond]
STANDINS = {"folder_paths": folder_paths, "comfy_extras.nodes_minimax_h3": minimax, "node_helpers": node_helpers}

_saved = {name: sys.modules.get(name) for name in
          ("comfy.model_management", "folder_paths", "ripple_core", "load_video_node", "save_video_node",
           "frame_number_node")}
sys.modules["comfy.model_management"] = types.SimpleNamespace(throw_exception_if_processing_interrupted=Mock())
sys.modules["folder_paths"] = folder_paths
try:
    spec = importlib.util.spec_from_file_location("wepenerd_ripple_test", ROOT / "ripple_node.py")
    ripple = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ripple)
    import save_video_node  # imported now so it binds the stand-ins too (Ripple Output saves through it)
    MODULES = {"ripple_core": ripple.rc, "load_video_node": ripple.lv, "save_video_node": save_video_node}
finally:
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
rc = ripple.rc


def write_numbered(path, frames, fps, size=(96, 64), gop=250, audio=False):
    """Lossless video whose frame i encodes i in its pixels (see ids)."""
    with av.open(str(path), "w") as out:
        stream = out.add_stream("ffv1", rate=fractions.Fraction(fps).limit_denominator(1001))
        stream.width, stream.height = size
        stream.pix_fmt = "bgr0"
        stream.codec_context.gop_size = gop
        sound = out.add_stream("pcm_s16le", rate=8000, layout="mono") if audio else None
        for i in range(frames):
            pixels = np.zeros((size[1], size[0], 3), np.uint8)
            pixels[..., 2], pixels[..., 1] = i % 256, i // 256
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
        if sound:
            samples = round(frames / fps * 8000)
            tone = (np.sin(np.arange(samples) / 8000 * 2 * math.pi * 440) * 8000).astype(np.int16)[None]
            frame = av.AudioFrame.from_ndarray(tone, format="s16", layout="mono")
            frame.sample_rate, frame.pts = 8000, 0
            for packet in sound.encode(frame):
                out.mux(packet)
            for packet in sound.encode():
                out.mux(packet)
    return path


def ids(frames):
    return [int(f[..., 1].mean().round()) * 256 + int(f[..., 2].mean().round()) for f in frames]


class FakeClip:
    def __init__(self):
        self.encodes = 0

    def tokenize(self, text, **kwargs):
        assert not kwargs.get("images"), "no guide may be shown to the text encoder"
        return text

    def encode_from_tokens_scheduled(self, tokens):
        self.encodes += 1
        return [[torch.zeros(1, 4, 8), {"text": tokens}]]


class FakeVAE:
    def __init__(self):
        self.frames_encoded = []

    def encode(self, images):
        self.frames_encoded.append(images.shape[0])
        frames = 1 if images.shape[0] == 1 else 2 + 5 * ((images.shape[0] - 5) // 17)
        return torch.full((1, 24, frames, 2, 2), float(images[..., 2].mean()))


class RippleTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        folder_paths.ROOT = self.folder.name
        self.modules = patch.dict(sys.modules, {**STANDINS, **MODULES})
        self.modules.start()
        for cache in (ripple._window_cache, ripple._prompt_cache, ripple._source_cache, ripple._info_cache):
            cache.clear()
        ripple.lv._list_cache.clear()

    def tearDown(self):
        self.modules.stop()
        self.folder.cleanup()

    def ripple_context(self, markers, start=0):
        window = torch.rand(22, 64, 96, 3)
        return {"window": window, "markers": markers, "edit_frame": markers[0]["frame"], "length": 22,
                "width": 96, "height": 64, "fps": 24, "audio": None, "window_start": start,
                "window_key": ("w", start)}


class DecodeTest(RippleTest):
    def test_grid_window_matches_round_near_grid(self):
        for fps, start, length in [(24, 0, 39), (24, 150, 56), (30, 0, 22), (30, 173, 39), (25, 61, 22),
                                   (60, 200, 22)]:
            with self.subTest(fps=fps, start=start):
                path = write_numbered(self.root / f"c{fps}.mkv", fps * 12, fps)
                frames, t0 = ripple.decode_grid_window(path, start, length)
                # FFmpeg fps (round=near) keeps the last source frame that starts before (k + 0.5) / 24:
                # the frame a browser shows when seeking to just before that point (js seekTime).
                expected = [min(fps * 12 - 1, math.ceil((k + 0.5) * fps / 24) - 1)
                            for k in range(start, start + length)]
                self.assertEqual(ids(frames), expected)
                self.assertLess(abs(t0 - start / 24), 1e-6)

    def test_seek_path_equals_decode_from_top(self):
        path = write_numbered(self.root / "g.mkv", 30 * 12, 30, gop=48)
        whole, _ = ripple.decode_grid_window(path, 0, 280)
        part, _ = ripple.decode_grid_window(path, 190, 56)
        self.assertEqual(ids(part), ids(whole[190:246]))

    def test_probe_reports_grid_length_and_a_version_token(self):
        path = write_numbered(self.root / "p.mkv", 24 * 5, 24, size=(64, 96))
        info = ripple.probe(path)
        self.assertEqual((info["grid_frames"], info["width"], info["height"]), (120, 64, 96))
        self.assertFalse(info["audio"])
        self.assertRegex(info["version"], r"^[0-9a-f]+-[0-9a-f]+$")

    def test_lists_videos_through_load_video(self):
        (self.root / "sub").mkdir()
        write_numbered(self.root / "sub" / "a.mkv", 10, 24)
        (self.root / "notes.txt").write_text("x")
        self.assertEqual(ripple.list_videos(), ["sub/a.mkv"])

    def test_video_must_stay_inside_the_input_folder(self):
        with self.assertRaises(ValueError):
            ripple.video_path("../outside.mkv")


class ClipTest(RippleTest):
    def test_clip_node_end_to_end(self):
        write_numbered(self.root / "v.mkv", 24 * 10, 24, size=(192, 108))
        out = ripple.WN_RippleClip().select("v.mkv", "107 frames (4.46 s)", rc.DEFAULT_RESOLUTION, 200, 50, "")
        frame, context, window, audio = out["result"]
        self.assertEqual((context["window_start"], context["edit_frame"]), (240 - 107, 50))
        self.assertEqual(tuple(window.shape), (107, 384, 672, 3))
        self.assertEqual(tuple(frame.shape), (1, 384, 672, 3))
        self.assertTrue(torch.equal(frame[0], window[50]))
        self.assertEqual(out["ui"]["binding"], [133, 107, [50], "v.mkv"])
        self.assertIsNone(audio)
        self.assertIn("Window moved", " ".join(out["ui"]["text"]))

    def test_new_nodes_start_with_two_markers(self):
        default = ripple.WN_RippleClip.INPUT_TYPES()["optional"]["markers"][1]["default"]
        self.assertEqual([m["frame"] for m in json.loads(default)], [26, 80])
        write_numbered(self.root / "v.mkv", 24 * 6, 24)
        out = ripple.WN_RippleClip().select("v.mkv", rc.DEFAULT_LENGTH_LABEL, rc.DEFAULT_RESOLUTION, 0, 0, default)
        self.assertEqual(out["ui"]["binding"][2], [26, 80])

    def test_one_frame_per_marker_in_time_order(self):
        write_numbered(self.root / "v.mkv", 24 * 10, 24, size=(192, 108))
        markers = json.dumps([{"frame": 60, "edited": ""}, {"frame": 5}, {"frame": 60}])
        out = ripple.WN_RippleClip().select("v.mkv", "107 frames (4.46 s)", rc.DEFAULT_RESOLUTION, 0, 0, markers)
        frames, context, window, _ = out["result"]
        self.assertEqual([m["frame"] for m in context["markers"]], [5, 60])
        self.assertTrue(torch.equal(frames[0], window[5]) and torch.equal(frames[1], window[60]))
        self.assertTrue(any("Two markers" in t for t in out["ui"]["text"]))

    def test_changing_only_the_markers_reuses_the_decoded_window(self):
        write_numbered(self.root / "v.mkv", 24 * 6, 24, audio=True)
        clip = ripple.WN_RippleClip()
        first = clip.select("v.mkv", "56 frames (2.33 s)", rc.DEFAULT_RESOLUTION, 10, 0, json.dumps([3, 40]))
        with patch.object(ripple, "decode_grid_window", side_effect=AssertionError("decoded again")):
            second = clip.select("v.mkv", "56 frames (2.33 s)", rc.DEFAULT_RESOLUTION, 10, 0,
                                 json.dumps([{"frame": 3, "edited": "ripple_edits/x.png", "edited_at": 13}, 40]))
        self.assertIs(first["result"][2], second["result"][2])
        self.assertEqual(first["result"][1]["window_key"], second["result"][1]["window_key"])
        audio = second["result"][3]
        self.assertEqual(audio["sample_rate"], 8000)
        self.assertEqual(audio["waveform"].shape[-1], round(56 / 24 * 8000))
        # moving the window decodes again
        third = clip.select("v.mkv", "56 frames (2.33 s)", rc.DEFAULT_RESOLUTION, 11, 0, json.dumps([3, 40]))
        self.assertNotEqual(third["result"][1]["window_key"], first["result"][1]["window_key"])

    def test_untrained_size_and_length_are_flagged(self):
        write_numbered(self.root / "v.mkv", 24 * 6, 24)
        out = ripple.WN_RippleClip().select("v.mkv", "107 frames (4.46 s)", "544 (trained up to 56 frames)", 0, 0, "")
        self.assertTrue(any("trained up to 56 frames" in t for t in out["ui"]["text"]))
        self.assertIsNone(rc.combination_note("352 (trained up to 124 frames)", 124))
        self.assertIsNotNone(rc.combination_note(rc.DEFAULT_RESOLUTION, 124))
        self.assertIsNotNone(rc.combination_note("768 (untested)", 22))

    def test_frame_png_matches_the_clip_output(self):
        write_numbered(self.root / "v.mkv", 24 * 10, 24, size=(192, 108))
        data, name = ripple.frame_png("v.mkv", 77, rc.DEFAULT_RESOLUTION)
        image = np.asarray(Image.open(io.BytesIO(data)))
        out = ripple.WN_RippleClip().select("v.mkv", "22 frames (0.92 s)", rc.DEFAULT_RESOLUTION, 70, 7, "")
        self.assertEqual((image.shape, name), ((384, 672, 3), "v_f00077.png"))
        diff = np.abs(image.astype(int) - (out["result"][0][0].numpy() * 255).round().astype(int)).max()
        self.assertLessEqual(diff, 1)


class ApplyTest(RippleTest):
    def test_one_keyframe_per_marker_from_the_input_batch(self):
        context = self.ripple_context([{"frame": 3, "edited": ""}, {"frame": 17, "edited": ""}])
        edits = torch.stack([context["window"][3], context["window"][17]]) * 0.5
        out = ripple.WN_RippleApply().apply(FakeClip(), FakeVAE(), context, "prompt", edited_images=edits)
        (cond,), _latent = out["result"]
        keyframes = cond[1]["minimax_keyframes"]
        self.assertEqual([k["resolved_frame_index"] for k in keyframes], [0, 3, 17])
        self.assertEqual(keyframes[0]["latent"].shape[2], 7)
        self.assertTrue(all(k["latent"].shape[2] == 1 for k in keyframes[1:]))
        self.assertIn("2 edited frames", out["ui"]["text"][0])

    def test_attached_edits_and_missing_ones(self):
        (self.root / "ripple_edits").mkdir()
        Image.fromarray(np.full((64, 96, 3), 200, np.uint8)).save(self.root / "ripple_edits" / "a.png")
        context = self.ripple_context([{"frame": 2, "edited": "ripple_edits/a.png"}, {"frame": 9, "edited": ""}])
        vae = FakeVAE()
        with self.assertRaisesRegex(ValueError, "No edited image for frame 10"):
            ripple.WN_RippleApply().apply(FakeClip(), vae, context, "prompt")
        self.assertEqual(vae.frames_encoded, [], "nothing is encoded before every marker has an edit")
        context["markers"][1]["edited"] = "ripple_edits/a.png"
        out = ripple.WN_RippleApply().apply(FakeClip(), vae, context, "prompt")
        keyframes = out["result"][0][0][1]["minimax_keyframes"]
        self.assertEqual([k["resolved_frame_index"] for k in keyframes], [0, 2, 9])
        self.assertLess(abs(float(keyframes[1]["latent"].mean()) - 200 / 255), 1e-3)
        # a batch input overrides the attachment for the markers it covers
        out = ripple.WN_RippleApply().apply(FakeClip(), vae, context, "prompt",
                                            edited_images=torch.zeros(1, 64, 96, 3))
        keyframes = out["result"][0][0][1]["minimax_keyframes"]
        self.assertEqual(float(keyframes[1]["latent"].mean()), 0.0)
        self.assertLess(abs(float(keyframes[2]["latent"].mean()) - 200 / 255), 1e-3)
        self.assertTrue(any("1 image(s) for 2 marker(s)" in t for t in out["ui"]["text"]))

    def test_new_edits_reuse_the_encoded_prompt_and_source(self):
        clip, vae = FakeClip(), FakeVAE()
        context = self.ripple_context([{"frame": 3, "edited": ""}, {"frame": 17, "edited": ""}])
        node = ripple.WN_RippleApply()
        node.apply(clip, vae, context, "prompt", edited_images=torch.rand(2, 64, 96, 3))
        node.apply(clip, vae, context, "prompt", edited_images=torch.rand(2, 64, 96, 3))
        self.assertEqual(clip.encodes, 1)
        self.assertEqual(vae.frames_encoded, [1, 1, 22, 1, 1])
        node.apply(clip, vae, context, "another prompt", edited_images=torch.rand(2, 64, 96, 3))
        self.assertEqual(clip.encodes, 2)
        moved = {**context, "window_key": ("w", 5)}
        node.apply(clip, vae, moved, "another prompt", edited_images=torch.rand(2, 64, 96, 3))
        self.assertEqual(vae.frames_encoded[-1], 22, "a different window is encoded again")
        node.apply(FakeClip(), FakeVAE(), moved, "another prompt", edited_images=torch.rand(2, 64, 96, 3))
        self.assertEqual(clip.encodes, 2, "a different CLIP/VAE does not use the cache")

    def test_moved_and_unchanged_edits_are_reported(self):
        (self.root / "ripple_edits").mkdir()
        context = self.ripple_context([{"frame": 2, "edited": "ripple_edits/a.png", "edited_at": 12},
                                       {"frame": 9, "edited": "ripple_edits/b.png", "edited_at": 19}], start=10)
        for name, frame in (("a.png", 2), ("b.png", 9)):
            pixels = (context["window"][frame].numpy() * 255).round().astype(np.uint8)
            Image.fromarray(pixels).save(self.root / "ripple_edits" / name)
        out = ripple.WN_RippleApply().apply(FakeClip(), FakeVAE(), context, "prompt")
        text = " ".join(out["ui"]["text"])
        self.assertNotIn("made for another frame", text)
        self.assertIn("No edited frame differs from the source", text)
        context["markers"][1]["edited_at"] = 25
        text = " ".join(ripple.WN_RippleApply().apply(FakeClip(), FakeVAE(), context, "prompt")["ui"]["text"])
        self.assertIn("Frame 10: its edit was made for another frame", text)

    def test_a_single_edited_frame_suggests_a_second(self):
        context = self.ripple_context([{"frame": 3, "edited": ""}])
        out = ripple.WN_RippleApply().apply(FakeClip(), FakeVAE(), context, "prompt",
                                            edited_images=torch.rand(1, 64, 96, 3))
        self.assertTrue(any("Add a second marker" in t for t in out["ui"]["text"]))

    def test_attached_edit_must_stay_inside_the_input_folder(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            ripple.load_input_image("../outside.png")


class OutputTest(RippleTest):
    def test_comparison_marks_every_edited_frame(self):
        context = {"window": torch.zeros(22, 32, 48, 3), "length": 22, "edit_frame": 4, "audio": None, "fps": 24,
                   "markers": [{"frame": 4}, {"frame": 15}]}
        images, audio, fps, comparison = ripple.WN_RippleOutput().output(torch.ones(22, 32, 48, 3), context,
                                                                         save="nothing")
        self.assertEqual((tuple(comparison.shape), fps), ((22, 32, 96, 3), 24.0))
        self.assertEqual([float(comparison[f, 0, 0, 0]) for f in (4, 15, 10)], [1.0, 1.0, 0.0])

    def test_saves_the_shot_with_audio_and_a_silent_comparison(self):
        rate, samples = 8000, round(22 / 24 * 8000)
        audio = {"waveform": torch.rand(1, 1, samples) * 0.2, "sample_rate": rate}
        context = {"window": torch.zeros(22, 32, 48, 3), "length": 22, "edit_frame": 4, "audio": audio, "fps": 24,
                   "markers": [{"frame": 4}]}
        with patch.object(save_video_node, "ProgressBar", Mock()):
            out = ripple.WN_RippleOutput().output(torch.rand(22, 32, 48, 3), context, save="shot + comparison",
                                                  filename_prefix="ripple/edit")
        previews = out["ui"]["images"]
        self.assertEqual([p["filename"] for p in previews], ["edit_00001_.mp4", "edit_compare_00001_.mp4"])
        shot, compare = (self.root / "output" / "ripple" / p["filename"] for p in previews)
        with av.open(str(shot)) as container:
            self.assertEqual(len(container.streams.audio), 1)
            stream = container.streams.video[0]
            self.assertEqual((stream.width, stream.height, stream.frames or 22), (48, 32, 22))
        with av.open(str(compare)) as container:
            self.assertEqual((len(container.streams.audio), container.streams.video[0].width), (0, 96))
        self.assertIs(out["result"][1], audio)


class MarkerRulesTest(unittest.TestCase):
    def test_markers_keep_their_edit_frame_token(self):
        markers, _ = rc.parse_markers(json.dumps([{"frame": 9, "edited": "e.png", "edited_at": 19},
                                                  {"frame": 2, "edited": "", "edited_at": 5}]), 0, 22)
        self.assertEqual(markers, [{"frame": 2, "edited": "", "edited_at": None},
                                   {"frame": 9, "edited": "e.png", "edited_at": 19}])
        self.assertEqual(rc.stale_markers(markers, 10), [])
        self.assertEqual(rc.stale_markers(markers, 11), [9])

    def test_empty_markers_mean_one_at_edit_frame(self):
        self.assertEqual(rc.parse_markers("", 7, 22)[0], [{"frame": 7, "edited": "", "edited_at": None}])


if __name__ == "__main__":
    unittest.main()
