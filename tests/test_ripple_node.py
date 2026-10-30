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
    import save_video_node  # Bind the save module to the same stand-ins.
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
        markers, _ = rc.parse_markers(markers, 22)
        return {"window": window, "markers": markers, "geometry": rc.geometry(96, 64, 0.006144), "length": 22,
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
    def select(self, **kwargs):
        values = dict(video="v.mkv", duration=107/24, megapixels=.25, window_start=0,
                      markers=rc.DEFAULT_MARKERS_JSON)
        return ripple.WN_RippleClip().select(**{**values, **kwargs})

    def test_clip_preserves_source_framing_and_returns_unpadded_anchors(self):
        write_numbered(self.root / "v.mkv", 240, 24, size=(192, 108))
        out = self.select(window_start=200, markers=json.dumps([{"id": 1, "frame": 50}]))
        frame, context, window, audio, anchor1, anchor2 = out["result"]
        self.assertEqual(context["window_start"], 133)
        self.assertEqual(tuple(window.shape), (107, 128, 192, 3))
        self.assertEqual(tuple(frame.shape), (1, 108, 192, 3))
        self.assertTrue(torch.equal(frame, anchor1))
        self.assertEqual(len(anchor2), 0)
        self.assertTrue(torch.equal(frame[0], window[50, 10:118]))
        self.assertEqual(out["ui"]["binding"], [133, 107, [50], "v.mkv"])
        self.assertIsNone(audio)

    def test_clean_schema_and_default_marker(self):
        inputs = ripple.WN_RippleClip.INPUT_TYPES()
        self.assertEqual(list(inputs["required"]), ["video", "duration", "megapixels", "window_start", "markers"])
        self.assertEqual(json.loads(inputs["required"]["markers"][1]["default"])[0]["id"], 1)
        self.assertEqual(ripple.WN_RippleClip.RETURN_NAMES[-2:], ("anchor_1", "anchor_2"))
        write_numbered(self.root / "v.mkv", 240, 24)
        self.assertIs(ripple.WN_RippleClip.VALIDATE_INPUTS("v.mkv", 12.0, 1.3), True)
        self.assertIsInstance(ripple.WN_RippleClip.VALIDATE_INPUTS("v.mkv", 0.0, .25), str)

    def test_anchor_identity_survives_crossing(self):
        write_numbered(self.root / "v.mkv", 240, 24)
        out = self.select(markers=json.dumps([{"id": 1, "frame": 60}, {"id": 2, "frame": 5}]))
        frames, context, _, _, anchor1, anchor2 = out["result"]
        self.assertEqual([(m["id"], m["frame"]) for m in context["markers"]], [(2, 5), (1, 60)])
        self.assertTrue(torch.equal(anchor1, frames[1:2]))
        self.assertTrue(torch.equal(anchor2, frames[:1]))

    def test_marker_changes_reuse_window_but_crop_changes_decode(self):
        write_numbered(self.root / "v.mkv", 144, 24, audio=True)
        first = self.select(duration=56/24, window_start=10, markers=json.dumps([3, 40]))
        with patch.object(ripple, "decode_grid_window", side_effect=AssertionError("decoded again")):
            second = self.select(duration=56/24, window_start=10,
                                 markers=json.dumps([{"id": 1, "frame": 4}, {"id": 2, "frame": 40}]))
        self.assertIs(first["result"][2], second["result"][2])
        self.assertEqual(second["result"][3]["waveform"].shape[-1], round(56/24*8000))
        changed = self.select(duration=56/24, window_start=10, crop="[0.25,0,0.5,1]")
        self.assertNotEqual(changed["result"][1]["window_key"], first["result"][1]["window_key"])

    def test_frame_endpoint_geometry_matches_selected_anchor(self):
        write_numbered(self.root / "v.mkv", 144, 24, size=(320, 180))
        settings = dict(megapixels=.012, crop="[0.25,0.1,0.5,0.8]", allow_upscale=False,
                        resize_method="lanczos", output_width=0, output_height=0)
        data, name = ripple.frame_png("v.mkv", 77, **settings)
        exported = np.asarray(Image.open(io.BytesIO(data)))
        out = self.select(duration=22/24, window_start=70, markers=json.dumps([{"id": 1,"frame": 7}]), **settings)
        pixels = (out["result"][4][0].numpy()*255).round().astype(np.uint8)
        np.testing.assert_array_equal(exported, pixels)
        g=out["result"][1]["geometry"]
        self.assertEqual(exported.shape[:2], (g["content_rect"][3], g["content_rect"][2]))
        self.assertEqual(name, "v_f00077.png")
        self.assertEqual(ripple.query_geometry({"megapixels":".012", "allow_upscale":"true"})["allow_upscale"], True)

    def test_large_source_is_resized_per_decoded_frame(self):
        write_numbered(self.root / "v.mkv", 22, 24, size=(1920, 1080))
        with patch.object(ripple, "transform_pixels", wraps=ripple.transform_pixels) as transform:
            out = self.select(duration=22/24, megapixels=.1)
        self.assertEqual(transform.call_count, 22)
        window = out["result"][2]
        self.assertLess(window.numel()*window.element_size(), 35_000_000)
        self.assertLess(out["result"][1]["width"], 500)

    def test_codec_crop_pixels_and_edge_padding_are_consistent(self):
        pixels=np.zeros((80,128,3),np.uint8)
        pixels[:,:,0]=np.arange(128,dtype=np.uint8)[None,:]
        pixels[:,:,1]=np.arange(80,dtype=np.uint8)[:,None]
        with av.open(str(self.root/"v.mkv"),"w") as out:
            stream=out.add_stream("ffv1",rate=24)
            stream.width,stream.height,stream.pix_fmt=128,80,"bgr0"
            for i in range(22):
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels,format="rgb24")): out.mux(packet)
            for packet in stream.encode(): out.mux(packet)
        out=self.select(duration=22/24,crop="[0.25,0.25,0.5,0.5]")
        expected=pixels[20:60,32:96]
        actual=(out["result"][4][0].numpy()*255).round().astype(np.uint8)
        np.testing.assert_array_equal(actual,expected)
        window=(out["result"][2][0].numpy()*255).round().astype(np.uint8)
        self.assertEqual(window.shape,(64,64,3))
        np.testing.assert_array_equal(window[:12],np.repeat(expected[:1],12,axis=0))
        np.testing.assert_array_equal(window[-12:],np.repeat(expected[-1:],12,axis=0))


class GeometryTest(unittest.TestCase):
    def test_duration_has_no_dataset_cap_and_fits_source(self):
        self.assertEqual(rc.duration_frames(107/24), 107)
        self.assertEqual(rc.duration_frames(20), 481)
        self.assertEqual(rc.fit_window(240, 200, 481)[:2], (14, 226))

    def test_crop_and_no_upscale_preserve_content_with_minimal_padding(self):
        g=rc.geometry(1920,1080,.25,crop=[.25,0,.5,1])
        self.assertEqual(g["source_rect"], [480,0,960,1080])
        x,y,w,h=g["content_rect"]
        self.assertLessEqual(w*h,250000)
        self.assertLess(g["width"]-w,32)
        self.assertLess(g["height"]-h,32)
        self.assertEqual((w%2,h%2,g["width"]%32,g["height"]%32),(0,0,0,0))
        small=rc.geometry(101,75,10)
        self.assertEqual(small["content_rect"][2:],[100,74])
        self.assertEqual(rc.normalized_crop(""),rc.normalized_crop([0,0,1,1]))

    def test_fit_bounds_and_optional_upscale(self):
        g=rc.geometry(1920,1080,2,output_width=800,output_height=800)
        self.assertEqual(g["content_rect"][2:],[800,450])
        bigger=rc.geometry(96,64,.25,allow_upscale=True)
        self.assertGreater(bigger["content_rect"][2],96)
        with self.assertRaises(ValueError): rc.geometry(96,64,.25,output_width=300)

    def test_full_frame_editor_rounding_is_restored_without_a_crop(self):
        g=rc.geometry(666,374,.25)
        image=torch.zeros(1,384,672,3)
        image[:,:,0:4,0]=1
        image[:,:,-4:,1]=1
        padded=ripple.edit_to_canvas(image,g)
        content=ripple.trim_content(padded,g)
        self.assertGreater(float(content[0,:,0,0].mean()),.7)
        self.assertGreater(float(content[0,:,-1,1].mean()),.7)


class ApplyTest(RippleTest):
    def test_explicit_anchor_ids_override_chronological_batch_after_crossing(self):
        context = self.ripple_context([{"id": 1, "frame": 17}, {"id": 2, "frame": 3}])
        out = ripple.WN_RippleApply().apply(FakeClip(), FakeVAE(), context, "prompt",
            edited_images=torch.full((2,64,96,3),.8),
            edited_anchor_1=torch.full((1,64,96,3),.2), edited_anchor_2=torch.full((1,64,96,3),.6))
        keyframes=out["result"][0][0][1]["minimax_keyframes"]
        self.assertEqual([k["resolved_frame_index"] for k in keyframes],[0,3,17])
        self.assertAlmostEqual(float(keyframes[1]["latent"].mean()),.6,places=3)
        self.assertAlmostEqual(float(keyframes[2]["latent"].mean()),.2,places=3)

    def test_mixed_explicit_batch_and_uploaded_edits(self):
        Image.fromarray(np.full((64,96,3),153,np.uint8)).save(self.root/"edit.png")
        context=self.ripple_context([{"id":1,"frame":2,"edited":"edit.png"},
                                     {"id":2,"frame":8,"edited":"edit.png"},
                                     {"id":3,"frame":17,"edited":"edit.png"}])
        out=ripple.WN_RippleApply().apply(FakeClip(),FakeVAE(),context,"prompt",
            edited_images=torch.full((2,64,96,3),.8),edited_anchor_1=torch.full((1,64,96,3),.2))
        values=[float(k["latent"].mean()) for k in out["result"][0][0][1]["minimax_keyframes"][1:]]
        np.testing.assert_allclose(values,[.2,.8,.6],atol=.004)

    def test_explicit_second_anchor_requires_marker_and_exactly_one_image(self):
        context=self.ripple_context([{"id":1,"frame":0}])
        vae=FakeVAE()
        with self.assertRaisesRegex(ValueError,"needs marker 2"):
            ripple.WN_RippleApply().apply(FakeClip(),vae,context,"prompt",edited_anchor_2=torch.rand(1,64,96,3))
        with self.assertRaisesRegex(ValueError,"exactly one"):
            ripple.WN_RippleApply().apply(FakeClip(),vae,context,"prompt",edited_anchor_1=torch.rand(2,64,96,3))
        self.assertEqual(vae.frames_encoded,[])

    def test_changed_crop_is_reported_for_upload_but_not_connected_override(self):
        Image.fromarray(np.full((64,96,3),153,np.uint8)).save(self.root/"edit.png")
        context=self.ripple_context([{"id":1,"frame":2,"edited":"edit.png","edited_crop":[.25,0,.5,1]}])
        out=ripple.WN_RippleApply().apply(FakeClip(),FakeVAE(),context,"prompt")
        self.assertIn("another frame, crop or source", " ".join(out["ui"]["text"]))
        out=ripple.WN_RippleApply().apply(FakeClip(),FakeVAE(),context,"prompt",edited_anchor_1=torch.rand(1,64,96,3))
        self.assertNotIn("another frame, crop or source", " ".join(out["ui"]["text"]))

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
        # Batch images override attachments for the markers they cover.
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

    def test_a_single_edited_frame_needs_no_note(self):
        context = self.ripple_context([{"frame": 3, "edited": ""}])
        out = ripple.WN_RippleApply().apply(FakeClip(), FakeVAE(), context, "prompt",
                                            edited_images=torch.rand(1, 64, 96, 3))
        self.assertEqual(out["ui"]["text"][0][:16], "1 edited frame o")
        self.assertEqual([k["resolved_frame_index"] for k in out["result"][0][0][1]["minimax_keyframes"]], [0, 3])

    def test_attached_edit_must_stay_inside_the_input_folder(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            ripple.load_input_image("../outside.png")


class OutputTest(RippleTest):
    def test_padding_is_trimmed_from_shot_and_comparison(self):
        g=rc.geometry(192,108,.25)
        context={"window":torch.zeros(22,128,192,3),"length":22,"audio":None,"fps":24,
                 "markers":[{"id":1,"frame":4}],"geometry":g}
        images,audio,fps,compare=ripple.WN_RippleOutput().output(torch.ones(22,128,192,3),context,save="nothing")
        self.assertEqual(tuple(images.shape),(22,108,192,3))
        self.assertEqual(tuple(compare.shape),(22,108,384,3))

    def test_comparison_preserves_pixels_by_default_and_marks_anchors_only_when_enabled(self):
        context = {"window": torch.zeros(22, 32, 48, 3), "length": 22, "edit_frame": 4, "audio": None, "fps": 24,
                   "markers": [{"frame": 4}, {"frame": 15}], "geometry": {"width":48,"height":32,"content_rect":[0,0,48,32]}}
        generated = torch.ones(22, 32, 48, 3)
        images, audio, fps, comparison = ripple.WN_RippleOutput().output(generated, context, save="nothing")
        self.assertEqual((tuple(comparison.shape), fps), ((22, 32, 96, 3), 24.0))
        self.assertTrue(torch.equal(comparison[:, :, :48], context["window"]))
        self.assertTrue(torch.equal(comparison[:, :, 48:], generated))
        marked = ripple.WN_RippleOutput().output(generated, context, save="nothing", show_anchor_markers=True)[3]
        for frame in (4, 15):
            torch.testing.assert_close(marked[frame, :6], torch.tensor([1., .8, 0.]).expand(6, 96, 3))
        self.assertTrue(torch.equal(marked[:, 6:], comparison[:, 6:]))
        self.assertTrue(torch.equal(marked[10], comparison[10]))
        self.assertEqual(float(context["window"].sum()), 0.)
        self.assertTrue(torch.equal(images, generated))

    def test_saves_the_shot_with_audio_and_a_silent_comparison(self):
        rate, samples = 8000, round(22 / 24 * 8000)
        audio = {"waveform": torch.rand(1, 1, samples) * 0.2, "sample_rate": rate}
        context = {"window": torch.zeros(22, 32, 48, 3), "length": 22, "edit_frame": 4, "audio": audio, "fps": 24,
                   "markers": [{"frame": 4}], "geometry": {"width":48,"height":32,"content_rect":[0,0,48,32]}}
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
            anchor = next(frame for index, frame in enumerate(container.decode(video=0)) if index == 4)
            self.assertLess(int(anchor.to_ndarray(format="rgb24")[:6, :24].max()), 8)
        self.assertIs(out["result"][1], audio)


class MarkerRulesTest(unittest.TestCase):
    def test_shorter_window_preserves_both_marker_ids_and_edits(self):
        markers,_=rc.parse_markers([{"id":1,"frame":60,"edited":"a.png","edited_at":60},
                                   {"id":2,"frame":90,"edited":"b.png","edited_at":90}],39)
        self.assertEqual({m["id"] for m in markers},{1,2})
        self.assertEqual({m["edited"] for m in markers},{"a.png","b.png"})
        self.assertEqual({m["frame"] for m in markers},{37,38})
        self.assertEqual(len(rc.stale_markers(markers,0)),2)

    def test_uploaded_source_identity_is_preserved_and_checked(self):
        markers,_=rc.parse_markers([{"id":1,"frame":0,"edited":"e.png",
                                    "edited_source":{"video":"a.mp4","version":"abc"}}],22)
        self.assertEqual(rc.stale_markers(markers,0,source="a.mp4",version="abc"),[])
        self.assertEqual(rc.stale_markers(markers,0,source="b.mp4",version="abc"),[0])
        self.assertEqual(rc.stale_markers(markers,0,source="a.mp4",version="new"),[0])
    def test_markers_keep_ids_and_provenance_after_sorting(self):
        markers,_=rc.parse_markers([{"id":1,"frame":9,"edited":"e.png","edited_at":19,"edited_crop":[0,0,1,1]},
                                    {"id":2,"frame":2}],22)
        self.assertEqual([m["id"] for m in markers],[2,1])
        self.assertEqual(rc.stale_markers(markers,10,[0,0,1,1]),[])
        self.assertEqual(rc.stale_markers(markers,11,[0,0,1,1]),[9])
        self.assertEqual(rc.stale_markers(markers,10,[.25,0,.5,1]),[9])

    def test_no_hidden_anchor_limit_and_default_one(self):
        self.assertEqual(len(rc.parse_markers([1,2,3,4,5,6],22)[0]),6)
        self.assertEqual(rc.parse_markers("",22)[0][0]["frame"],0)


class FromFramesTest(RippleTest):
    def test_v3_loader_resamples_frames_and_slices_audio(self):
        frames=torch.zeros(120,64,96,3)
        for i in range(120): frames[i,:,:,2]=i/255
        sound={"waveform":torch.arange(32000,dtype=torch.float32)[None,None],"sample_rate":8000}
        out=ripple.WN_RippleFromFrames.execute(frames,30,22/24,.25,24,
                json.dumps([{"id":1,"frame":17},{"id":2,"frame":3}]),audio=sound)
        selected,context,window,audio,a1,a2=out.result
        expected=[math.ceil((k+.5)*30/24)-1 for k in range(24,46)]
        self.assertEqual(ids((window.numpy()*255).round().astype(np.uint8)),expected)
        self.assertEqual(float(audio["waveform"][0,0,0]),8000)
        self.assertEqual(audio["waveform"].shape[-1],round(22/24*8000))
        self.assertTrue(torch.equal(a1,selected[1:2]))
        self.assertTrue(torch.equal(a2,selected[:1]))
        self.assertEqual(context["fps"],24)
        self.assertEqual(ripple.WN_RippleFromFrames.define_schema().node_id,"WN_RippleFromFrames")


if __name__ == "__main__":
    unittest.main()
