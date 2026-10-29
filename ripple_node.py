"""H3 Ripple nodes: pick a window and one or more frames, edit them anywhere, propagate the edit with H3.

Ripple Clip   - load a video, choose the generation window and the frames to edit on a timeline
                ("Add edited frame" adds markers). Each marker can carry an attached edited image.
                Outputs the exact frames to edit and a RIPPLE context (window frames, markers, size, audio).
Ripple Apply  - builds the H3 FL2VA conditioning the Ripple LoRA expects: prompt-only text,
                the original window as a guide at frame 0 and each edited image as its own
                single-image guide at its frame (the same keyframes chained Add Guide nodes make).
Ripple Output - saves the generated shot with its source audio and a source | result comparison
                (through Save Video), and passes the frames on.

Clip -> editor -> Apply is two nodes on purpose: a node cannot send an image to an editor and get
the result back (ComfyUI rejects cycles). Put Clip and Apply in one group.

Iterating is cheap: a re-run after changing only the edits re-uses the decoded window, the encoded
prompt and the encoded source clip, so it encodes just the edited frames before sampling.
"""

from __future__ import annotations

import itertools
import math
import weakref
from pathlib import Path

import av
import numpy as np
import torch

try:
    from . import ripple_core as rc
    from . import load_video_node as lv
except ImportError:  # loaded standalone by the tests
    import ripple_core as rc
    import load_video_node as lv

RIPPLE_TYPE = "WN_RIPPLE"


# ---------------------------------------------------------------------------
# Video access (input folder only)
# ---------------------------------------------------------------------------


def _input_root() -> Path:
    import folder_paths

    return Path(folder_paths.get_input_directory()).resolve()


def list_videos() -> list[str]:
    """Videos in the input folder (Load Video's cached listing, so big input folders stay fast)."""
    return lv.list_videos(_input_root()) or [""]


def video_path(video: str) -> Path:
    """The video's path, which must be inside the input folder (same rules as Load Video)."""
    return lv.video_path(video)


_info_cache: dict = {}


def probe(path: Path) -> dict:
    """Duration, size (after rotation), native fps, first-frame time and the 24 fps grid length."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key in _info_cache:
        return dict(_info_cache[key])
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("This file has no video stream.")
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.guessed_rate
        if not rate:
            raise ValueError("Could not read the video's frame rate.")
        start = (stream.start_time or 0) * stream.time_base
        if stream.duration:
            duration = float(stream.duration * stream.time_base)
        elif container.duration:
            duration = container.duration / av.time_base
        else:
            duration = (stream.frames or 0) / float(rate)
        first = next(container.decode(stream), None)
        width, height, rotation, first_time = stream.width, stream.height, 0, 0.0
        if first is not None:
            width, height, rotation = first.width, first.height, first.rotation
            if first.pts is not None:
                first_time = float(first.pts * first.time_base - start)
        if rotation % 180:
            width, height = height, width
    info = {
        "width": int(width), "height": int(height), "rotation": int(rotation), "fps": float(rate),
        "duration": float(duration), "first_time": first_time,
        "grid_frames": rc.grid_frame_count(max(0.0, duration - first_time)),
        "audio": None,
        # Changes when the file does: lets the browser cache exact-frame thumbnails safely.
        "version": f"{stat.st_size:x}-{stat.st_mtime_ns:x}",
    }
    with av.open(str(path)) as container:
        info["audio"] = bool(container.streams.audio)
    if len(_info_cache) > 64:
        _info_cache.clear()
    _info_cache[key] = info
    return dict(info)


def decode_grid_window(path: Path, start: int, length: int) -> tuple[np.ndarray, float]:
    """Frames ``start .. start+length-1`` of the source resampled to 24 fps, as uint8 [N, H, W, 3].

    The 24 fps grid starts at the first frame (FFmpeg fps filter, round=near): grid frame k is the
    last source frame starting before (k + 0.5) / 24, which is what the browser preview seeks to. Decoding jumps to a keyframe before the window; frames before the
    window are never converted to RGB. Also returns the window's start time in source seconds.
    """
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        tb = stream.time_base
        stream_start = (stream.start_time or 0) * tb
        first_pts = None
        for frame in container.decode(stream):
            first_pts = frame.pts
            break
        if first_pts is None:
            raise ValueError("The video has no decodable frames.")
        seek_seconds = start / rc.FPS - 1.0
        container.seek(0, stream=stream) if seek_seconds <= 0 else container.seek(
            max(0, math.floor((stream_start + seek_seconds) / tb)), stream=stream, backward=True
        )
        graph = None
        frames: list[np.ndarray] = []
        rotation = 0
        for frame in itertools.chain(container.decode(stream), (None,)):
            if frame is not None:
                if frame.pts is None:
                    raise ValueError("The video has no frame timestamps.")
                rotation = frame.rotation
            if graph is None:
                if frame is None:
                    break
                graph = av.filter.Graph()
                graph.link_nodes(
                    graph.add_buffer(width=frame.width, height=frame.height, format=frame.format, time_base=frame.time_base),
                    graph.add("setpts", f"PTS-{first_pts}"),
                    graph.add("fps", f"fps={rc.FPS}:start_time=0:round=near"),
                    graph.add("buffersink"),
                )
                graph.configure()
            graph.push(frame)
            while len(frames) < length:
                try:
                    out = graph.pull()
                except (av.error.BlockingIOError, av.error.EOFError):
                    break
                index = int(out.pts)
                if index < start:
                    continue
                pixels = out.to_ndarray(format="rgb24")
                if rotation:
                    pixels = np.rot90(pixels, rotation // 90)
                frames.append(np.ascontiguousarray(pixels))
            if len(frames) >= length:
                break
    if not frames:
        raise ValueError(f"No frames at 24 fps grid position {start}; the window is past the end of the video.")
    window_time = float((first_pts * tb - stream_start)) + start / rc.FPS
    return np.stack(frames), window_time


def decode_audio(path: Path, start_seconds: float, duration: float) -> dict | None:
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        rate = stream.codec_context.sample_rate
        channels = stream.codec_context.channels
        stream_start = float((stream.start_time or 0) * stream.time_base)
        first, count = round((start_seconds + stream_start) * rate), round(duration * rate)
        waveform = np.zeros((channels, count), dtype=np.float32)
        resampler = av.AudioResampler(format="fltp", layout=stream.codec_context.layout, rate=rate)
        if start_seconds > 0.5:
            container.seek(int((stream_start + start_seconds - 0.5) / stream.time_base), stream=stream, backward=True)
        for frame in itertools.chain(container.decode(stream), (None,)):
            for samples in resampler.resample(frame):
                if samples.pts is None:
                    continue
                offset = round(samples.pts * samples.time_base * rate) - first
                if offset >= count:
                    return {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": rate}
                lo, hi = max(0, offset), min(count, offset + samples.samples)
                if hi > lo:
                    waveform[:, lo:hi] = samples.to_ndarray()[:, lo - offset:hi - offset]
        return {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": rate}


def load_input_image(relative: str) -> torch.Tensor:
    """An attached edited image from ComfyUI's input folder as [1, H, W, 3] floats."""
    from PIL import Image, ImageOps

    root = _input_root()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Attached edited image not found in the input folder: {relative}")
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        return torch.from_numpy(np.asarray(image, dtype=np.float32) / 255.0)[None]


def frame_png(video: str, absolute_frame: int, resolution: str) -> tuple[bytes, str]:
    """PNG of one 24 fps grid frame at the generation size, exactly as Ripple Clip outputs it."""
    import io
    from PIL import Image

    path = video_path(video)
    info = probe(path)
    absolute_frame = min(max(0, int(absolute_frame)), max(0, info["grid_frames"] - 1))
    frames, _ = decode_grid_window(path, absolute_frame, 1)
    width, height = rc.canvas_size(info["width"], info["height"], rc.short_edge(resolution))
    image = to_canvas(torch.from_numpy(frames[:1]).float().div_(255), width, height)[0]
    buffer = io.BytesIO()
    Image.fromarray((image.clamp(0, 1).numpy() * 255).round().astype(np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue(), f"{Path(video).stem}_f{absolute_frame:05d}.png"


def to_canvas(images: torch.Tensor, width: int, height: int) -> torch.Tensor:
    """Cover-resize and centre-crop [B, H, W, C] floats, exactly like the H3 guide nodes do."""
    import comfy.utils

    samples = images[..., :3].movedim(-1, 1)
    return comfy.utils.common_upscale(samples, width, height, "lanczos", "center").movedim(1, -1).clamp(0, 1)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _weak(obj):
    try:
        return weakref.ref(obj)
    except TypeError:
        return None


def _same(ref, obj) -> bool:
    return ref is not None and ref() is obj


# One entry each: the last decoded window, the last encoded prompt and the last encoded source clip.
# Re-running with new edits or a new seed then skips the video decode, the (large) text encoder and the
# whole-window VAE encode. Entries hold weak references to the CLIP/VAE so swapping models frees them.
_window_cache: dict = {}
_prompt_cache: dict = {}
_source_cache: dict = {}


def load_window(path: Path, info: dict, start: int, length: int, width: int, height: int) -> dict:
    """Decode, resize and slice the audio of one window; the last result is kept for re-runs."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns, start, length, width, height)
    if _window_cache.get("key") == key:
        return _window_cache["value"]
    frames, window_time = decode_grid_window(path, start, length)
    notes = []
    if len(frames) < length:  # the container overstated its duration
        shorter = 17 * ((len(frames) - 5) // 17) + 5
        if shorter < 5:
            raise ValueError(f"Only {len(frames)} frames could be decoded at frame {start}; H3 needs at least 5.")
        notes.append(f"Only {len(frames)} frames could be decoded here; using {shorter}.")
        frames, length = frames[:shorter], shorter
    window = to_canvas(torch.from_numpy(frames).float().div_(255), width, height)
    audio = decode_audio(path, window_time, length / rc.FPS) if info.get("audio") else None
    value = {"window": window, "length": length, "window_time": window_time, "audio": audio, "notes": notes,
             "key": key}
    _window_cache.clear()
    _window_cache.update(key=key, value=value)
    return value


def encode_prompt(clip, prompt: str):
    """Text-only conditioning (no guide is shown to the text encoder, as in training), cached per CLIP."""
    if _same(_prompt_cache.get("clip"), clip) and _prompt_cache.get("prompt") == prompt:
        return _prompt_cache["value"]
    value = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
    _prompt_cache.clear()
    _prompt_cache.update(clip=_weak(clip), prompt=prompt, value=value)
    return value


def encode_source(vae, ripple: dict):
    """The whole original window as one guide latent, cached per VAE and window."""
    key = ripple.get("window_key")
    if key is not None and _same(_source_cache.get("vae"), vae) and _source_cache.get("key") == key:
        return _source_cache["value"]
    value = vae.encode(ripple["window"][..., :3])
    _source_cache.clear()
    _source_cache.update(vae=_weak(vae), key=key, value=value)
    return value


class WN_RippleClip:
    CATEGORY = "WepeNerd/H3 Ripple"
    FUNCTION = "select"
    RETURN_TYPES = ("IMAGE", RIPPLE_TYPE, "IMAGE", "AUDIO")
    RETURN_NAMES = ("frames_to_edit", "ripple", "source_window", "audio")
    OUTPUT_TOOLTIPS = (
        "The exact frames to edit (one per marker, in time order), at the size H3 will generate.",
        "Window frames, edited-frame markers and size for Ripple Apply / Ripple Output.",
        "The original frames of the window at the generation size.",
        "The source audio of the window (None if the video has no audio).",
    )
    DESCRIPTION = (
        "Load a video, drag the blue window (the frames H3 will generate) along the timeline and move the two "
        "numbered markers to the frames you want to edit. Download each frame, make the same change in both, "
        "then upload, drop or paste the edits onto their cards. Arrow keys step the selected marker; Shift+arrows "
        "move the window; Tab switches markers; Ctrl+V pastes an edit onto the selected marker."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": (list_videos(), {"video_upload": True}),
                "length": (rc.LENGTH_LABELS, {"default": rc.DEFAULT_LENGTH_LABEL,
                           "tooltip": "Frames H3 generates (24 fps)."}),
                "resolution": (list(rc.RESOLUTIONS), {"default": rc.DEFAULT_RESOLUTION,
                               "tooltip": "Short edge of the generation; the long edge follows the video's aspect."}),
                "window_start": ("INT", {"default": 0, "min": 0, "max": 10_000_000,
                                 "tooltip": "First frame of the window on the video's 24 fps timeline. Set by the timeline."}),
                "edit_frame": ("INT", {"default": 0, "min": 0, "max": 1000,
                               "tooltip": "First edited frame inside the window (0 = first). Set by the timeline."}),
            },
            "optional": {
                "markers": ("STRING", {"default": rc.DEFAULT_MARKERS_JSON, "multiline": False,
                            "tooltip": "Edited-frame markers as JSON, managed by the timeline. Empty = one marker at edit_frame."}),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, video, length, resolution):
        # length and resolution are checked here rather than against the lists, so workflows saved with
        # older labels ("384 (...)") keep working.
        try:
            video_path(video)
            rc.parse_length(length)
        except ValueError as error:
            return str(error)
        if rc.short_edge(resolution) not in rc.RESOLUTIONS.values():
            return f"Unknown resolution: {resolution}"
        return True

    @classmethod
    def IS_CHANGED(cls, video, **kwargs):
        stat = video_path(video).stat()
        return (video, stat.st_size, stat.st_mtime_ns, tuple(sorted(kwargs.items())))

    def select(self, video, length, resolution, window_start, edit_frame, markers=""):
        path = video_path(video)
        info = probe(path)
        start, frames_n, edit, notes = rc.fit_window(info["grid_frames"], window_start, rc.parse_length(length), edit_frame)
        width, height = rc.canvas_size(info["width"], info["height"], rc.short_edge(resolution))
        loaded = load_window(path, info, start, frames_n, width, height)
        window, frames_n, audio = loaded["window"], loaded["length"], loaded["audio"]
        notes += loaded["notes"]
        marker_list, marker_notes = rc.parse_markers(markers, min(edit, frames_n - 1), frames_n)
        notes += marker_notes
        edits = [marker["frame"] for marker in marker_list]
        ripple = {
            "window": window, "markers": marker_list, "edit_frame": edits[0], "length": frames_n, "width": width,
            "height": height, "fps": rc.FPS, "window_start": start, "window_time": loaded["window_time"],
            "audio": audio, "source": video, "window_key": loaded["key"],
            "binding": (video, path.stat().st_mtime_ns, start, frames_n, tuple(edits), width, height),
        }
        text = [rc.describe(start, frames_n, edits) + f" · {width}x{height}", *notes]
        ui = {"text": text, "binding": [start, frames_n, edits, video]}
        return {"ui": ui, "result": (window[edits].clone(), ripple, window, audio)}


class WN_RippleApply:
    CATEGORY = "WepeNerd/H3 Ripple"
    FUNCTION = "apply"
    RETURN_TYPES = ("CONDITIONING", "LATENT")
    RETURN_NAMES = ("positive", "latent")
    OUTPUT_TOOLTIPS = (
        "H3 conditioning: prompt, original window guide at frame 0, each edited frame as a guide at its frame.",
        "Empty H3 audio-video latent for the window.",
    )
    DESCRIPTION = (
        "Builds the conditioning the H3 Ripple LoRA expects: the original window as a guide plus one "
        "guide per edited frame. Edited images come from the edits attached on Ripple Clip, or from the "
        "edited_images input (one image per marker, in time order), which takes precedence. Use the FL2VA model "
        "with the Ripple LoRA, a BasicGuider (CFG 1) and any H3 sampler. The default prompt works for every edit. "
        "Re-runs with new edits reuse the encoded prompt and source clip."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "ripple": (RIPPLE_TYPE,),
                "prompt": ("STRING", {"multiline": True, "default": rc.DEFAULT_PROMPT,
                           "tooltip": "Leave as is. The edits come from the images, not the text."}),
            },
            "optional": {
                "edited_images": ("IMAGE", {"tooltip": "Edited frames from an in-graph editor: one per marker, in time "
                                                       "order. Overrides the images attached on Ripple Clip."}),
            },
        }

    def apply(self, clip, vae, ripple, prompt, edited_images=None, edited_image=None):
        import node_helpers
        from comfy_extras.nodes_minimax_h3 import _empty_av_latent

        if edited_images is None:
            edited_images = edited_image  # input name used before multiple markers
        width, height, length = ripple["width"], ripple["height"], ripple["length"]
        markers = ripple.get("markers") or [{"frame": ripple["edit_frame"], "edited": ""}]
        window = ripple["window"]
        notes, checks, keyframes_edit, unchanged = [], [], [], 0
        if edited_images is not None and len(edited_images) != len(markers):
            notes.append(f"edited_images has {len(edited_images)} image(s) for {len(markers)} marker(s); "
                         "missing ones use the attached edits.")
        missing = [m["frame"] + 1 for i, m in enumerate(markers)
                   if not (edited_images is not None and i < len(edited_images)) and not m.get("edited")]
        if missing:
            frames = ", ".join(str(frame) for frame in missing)
            raise ValueError(f"No edited image for frame{'s' if len(missing) > 1 else ''} {frames}: upload, drop or "
                             "paste one on its Ripple Clip card, or connect edited_images.")
        stale = set(rc.stale_markers(markers, int(ripple.get("window_start", 0))))
        window_np = None
        for index, marker in enumerate(markers):
            frame, label = int(marker["frame"]), f"Frame {int(marker['frame']) + 1}"
            from_input = edited_images is not None and index < len(edited_images)
            edited = edited_images[index:index + 1] if from_input else load_input_image(marker["edited"])
            if frame in stale and not from_input:
                notes.append(f"{label}: its edit was made for another frame (the marker or window moved after "
                             "uploading). Re-export the frame or move the marker back.")
            aspect = rc.aspect_note((int(edited.shape[2]), int(edited.shape[1])), (width, height))
            if aspect:
                notes.append(f"{label}: {aspect}")
            edited = to_canvas(edited, width, height)
            if window_np is None:
                window_np = window.cpu().numpy()
            match = rc.match_edited_frame(window_np, edited[0].cpu().numpy(), frame)
            if match["unchanged"]:
                unchanged += 1
                notes.append(f"{label}: identical to the original frame, so that moment is kept as it is.")
            elif match["verdict"] == "mismatch":
                notes.append(f"{label}: the image looks like it was made from frame {match['best'] + 1}. "
                             "Re-export after moving the marker, or move it back.")
            checks.append("✓" if match["verdict"] == "ok" else "?")
            keyframes_edit.append({"resolved_frame_index": frame, "latent": vae.encode(edited[..., :3])})
        if unchanged == len(markers):
            notes.append("No edited frame differs from the source: the result will look like the original shot.")
        if len(markers) < rc.RECOMMENDED_MARKERS:
            notes.append("One edited frame: two carry an edit more reliably. Add a second marker for best results.")

        latent, frame_count = _empty_av_latent(width, height, length)
        if frame_count != length:
            raise ValueError(f"Window length {length} is not a valid H3 length")
        positive = encode_prompt(clip, prompt)
        keyframes = [{"resolved_frame_index": 0, "latent": encode_source(vae, ripple)}, *keyframes_edit]
        positive = node_helpers.conditioning_set_values(positive, {"minimax_keyframes": keyframes})
        where = ", ".join(f"{m['frame'] + 1} {c}" for m, c in zip(markers, checks))
        text = [f"{len(markers)} edited frame{'s' if len(markers) > 1 else ''} of {length} ({width}x{height}): "
                f"frames {where} (✓ = image matches its source frame)", *notes]
        return {"ui": {"text": text}, "result": (positive, latent)}


SAVE_CHOICES = ("shot + comparison", "shot", "comparison", "nothing")


def comparison_frames(images: torch.Tensor, ripple: dict) -> torch.Tensor:
    """Source | result side by side with a yellow bar on every edited frame."""
    count = min(len(images), ripple["length"])
    images = images[:count]
    source = ripple["window"][:count].to(images.device, images.dtype)
    if source.shape[1:3] != images.shape[1:3]:
        source = to_canvas(source, int(images.shape[2]), int(images.shape[1]))
    comparison = torch.cat((source, images[..., :3]), dim=2).clone()
    for marker in ripple.get("markers") or [{"frame": ripple["edit_frame"]}]:
        if marker["frame"] < count:
            comparison[marker["frame"], :6] = torch.tensor([1.0, 0.8, 0.0], dtype=comparison.dtype)
    return comparison


class WN_RippleOutput:
    CATEGORY = "WepeNerd/H3 Ripple"
    FUNCTION = "output"
    OUTPUT_NODE = True
    RETURN_TYPES = ("IMAGE", "AUDIO", "FLOAT", "IMAGE")
    RETURN_NAMES = ("images", "audio", "frame_rate", "comparison")
    OUTPUT_TOOLTIPS = (
        "The generated window.",
        "The source audio of the window.",
        "24.0",
        "Source | result side by side, with the edited frames marked.",
    )
    DESCRIPTION = (
        "Saves the generated shot as MP4 with the source audio, and a source | result comparison, using Save "
        "Video. Outputs the frames for anything further (another Save Video for other formats, an upscaler...)."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "ripple": (RIPPLE_TYPE,),
            },
            "optional": {
                "save": (list(SAVE_CHOICES), {"default": SAVE_CHOICES[0],
                         "tooltip": "What to save as MP4 (H.264) in the output folder. The comparison is silent."}),
                "filename_prefix": ("STRING", {"default": "ripple/edit",
                                    "tooltip": "Name prefix; may include subfolders. The comparison adds _compare."}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    def output(self, images, ripple, save=SAVE_CHOICES[0], filename_prefix="ripple/edit", prompt=None,
               extra_pnginfo=None):
        count = min(len(images), ripple["length"])
        images = images[:count]
        comparison = comparison_frames(images, ripple)
        fps, audio, previews = float(ripple["fps"]), ripple.get("audio"), []
        if save in ("shot + comparison", "shot", "comparison"):
            try:
                from . import save_video_node as sv
            except ImportError:  # loaded standalone by the tests
                import save_video_node as sv
            saver = sv.WN_SaveVideo()
            jobs = []
            if save != "comparison":
                jobs.append((images, filename_prefix, audio))
            if save != "shot":
                jobs.append((comparison, f"{filename_prefix}_compare", None))
            for frames, prefix, sound in jobs:
                saved = saver.save_video(frames, fps, prefix, audio=sound, prompt=prompt, extra_pnginfo=extra_pnginfo)
                previews += saved["ui"]["images"]
        result = (images, audio, fps, comparison)
        if not previews:
            return result
        return {"ui": {"images": previews, "animated": (True,)}, "result": result}


def register_ripple_routes():
    """/wepenerd/ripple/info: source facts for the timeline, without running the graph."""
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    instance = PromptServer.instance
    if instance is None or getattr(instance, "_wn_ripple_routes", False):
        return

    @instance.routes.get("/wepenerd/ripple/info")
    async def ripple_info(request):
        try:
            info = probe(video_path(request.query.get("video", "")))
        except (OSError, ValueError, av.error.FFmpegError) as error:
            return web.json_response({"error": str(error)}, status=404)
        return web.json_response(info)

    @instance.routes.get("/wepenerd/ripple/frame")
    async def ripple_frame(request):
        query = request.query
        try:
            data, name = frame_png(query.get("video", ""), int(query.get("frame", "0")), query.get("resolution", ""))
        except (OSError, ValueError, av.error.FFmpegError) as error:
            return web.json_response({"error": str(error)}, status=404)
        # Thumbnails carry the file's version token, so the browser may keep them; downloads never cache.
        cacheable = query.get("v") and not query.get("download")
        headers = {"Cache-Control": "private, max-age=86400" if cacheable else "no-store"}
        if query.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return web.Response(body=data, content_type="image/png", headers=headers)

    instance._wn_ripple_routes = True


NODE_CLASS_MAPPINGS = {
    "WN_RippleClip": WN_RippleClip,
    "WN_RippleApply": WN_RippleApply,
    "WN_RippleOutput": WN_RippleOutput,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_RippleClip": "H3 Ripple Clip (WepeNerd)",
    "WN_RippleApply": "H3 Ripple Apply (WepeNerd)",
    "WN_RippleOutput": "H3 Ripple Output (WepeNerd)",
}
