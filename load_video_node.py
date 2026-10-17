"""Load an uploaded video as a sampled RGB image batch."""

from fractions import Fraction
import itertools
import math
import os
from pathlib import Path
import time

import av
import numpy as np
import torch

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar

try:
    from .frame_number_node import load_presets
except ImportError:  # Loaded as a standalone module, as the tests do.
    from frame_number_node import load_presets


VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".gif"}
DEFAULT_FRAME_CAP = 121
# Format names used before the presets were shared with Duration Selector, mapped to the
# shared preset they became and the frame rule (step, offset) to fall back on if it is missing.
LEGACY_FORMATS = {
    "AnimateDiff": ("None", None),
    "Mochi": ("Mochi 1 (6n+1, 30 fps)", (6, 1)),
    "LTXV": ("LTX-Video 0.9.x (8n+1, 25 fps)", (8, 1)),
    "Hunyuan": ("HunyuanVideo (4n+1, 24 fps)", (4, 1)),
    "Cosmos": ("Cosmos 1.0 (8n+1, 24 fps)", (8, 1)),
    "Wan": ("Wan 2.1 / 2.2 14B (4n+1, 16 fps)", (4, 1)),
    "H3": ("MiniMax H3 (17n+5, 24 fps)", (17, 5)),
}
LIST_CACHE_SECONDS = 3.0
_list_cache = {}
_info_cache = {}


def video_path(video):
    root = Path(folder_paths.get_input_directory()).resolve()
    path = Path(folder_paths.get_annotated_filepath(video)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Video must be inside ComfyUI's input folder.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
        raise ValueError(f"Video file not found or unsupported: {video}")
    return path


def list_videos(root):
    """Video files under root, as sorted relative POSIX paths.

    os.scandir gets file types from the directory listing itself, so Windows needs no
    per-file stat call. Hidden folders (.cache, .git, ...) are skipped. The result is
    reused for a few seconds because ComfyUI asks for it again on every queued prompt.
    """
    root = str(root)
    now = time.monotonic()
    cached = _list_cache.get(root)
    if cached and now - cached[0] < LIST_CACHE_SECONDS:
        return list(cached[1])
    found, pending = [], [("", root)]
    while pending:
        prefix, folder = pending.pop()
        try:
            entries = os.scandir(folder)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.name.startswith("."):
                            pending.append((f"{prefix}{entry.name}/", entry.path))
                    elif os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS and entry.is_file():
                        found.append(prefix + entry.name)
                except OSError:
                    continue
    found.sort()
    _list_cache[root] = (now, found)
    return list(found)


def format_names():
    return ["None", *load_presets()]


def resolve_format(name, presets=None):
    """Return the frame rule for a format: None, {"rule": (step, offset)} or {"frames": [...]}.

    Also reports the preset's frame range and fps for the info readout.
    """
    if name == "None":
        return None
    presets = load_presets() if presets is None else presets
    legacy_rule = None
    if name not in presets and name in LEGACY_FORMATS:
        name, legacy_rule = LEGACY_FORMATS[name]
        if name == "None":
            return None
    if name in presets:
        preset = presets[name]
        return {"rule": preset["rule"], "frames": preset["frames"], "fps": preset["fps"],
                "min": preset["frames"][0], "max": preset["frames"][-1]}
    if legacy_rule:
        return {"rule": legacy_rule, "frames": None, "fps": None, "min": None, "max": None}
    raise ValueError(f"Unknown video format preset: {name}")


def largest_valid(count, preset):
    """Largest frame count <= count that the preset allows, or 0."""
    if preset is None or count <= 0:
        return max(0, count)
    if preset["rule"]:
        step, offset = preset["rule"]
        valid = count - (count - offset) % step
        return valid if valid >= 1 else 0
    return max((frames for frames in preset["frames"] if frames <= count), default=0)


def plan_clip(info, frame_rate=0, skip_first_frames=0, select_every_nth=1, frame_load_cap=0, preset=None):
    """Predict the output from the source's metadata without decoding (js/load_video.mjs mirrors this).

    ``estimate`` is True when the end of the file sets the count, because file lengths and frame
    totals in container headers can be approximate. A count set by frame_load_cap is exact.
    """
    rate = frame_rate or info["fps"]
    if frame_rate:
        available = math.floor(info["duration"] * frame_rate + 0.5)
    else:
        available = info["frames"]
    available = max(0, available)
    from_end = True
    selected = max(0, -(-(available - skip_first_frames) // select_every_nth))
    cap = largest_valid(frame_load_cap, preset) if frame_load_cap else 0
    if frame_load_cap and not cap:
        selected, from_end = 0, False  # The cap is below the preset's smallest count: the node stops with an error.
    elif cap and cap <= selected:
        # A cap within a frame of an estimated end may not be reached.
        from_end = available - skip_first_frames < (cap + 1) * select_every_nth
        selected = cap
    count = largest_valid(selected, preset)
    output_rate = rate / select_every_nth
    clip_start = skip_first_frames / rate
    seconds = count / output_rate
    return {"count": count, "frame_rate": output_rate, "seconds": seconds,
            "clip_start": clip_start, "clip_end": clip_start + seconds, "estimate": from_end}


def alpha_decoder(stream):
    """VP8/VP9 keep transparency in side data that only libvpx decodes; FFmpeg's own decoder drops it."""
    name = stream.codec_context.name
    alpha = {key.lower(): value for key, value in stream.metadata.items()}.get("alpha_mode")
    if name not in ("vp8", "vp9") or str(alpha) != "1":
        return stream.codec_context
    try:
        decoder = av.CodecContext.create("libvpx-vp9" if name == "vp9" else "libvpx", "r")
    except (ValueError, av.error.FFmpegError):
        return stream.codec_context
    if stream.codec_context.extradata:
        decoder.extradata = stream.codec_context.extradata
    return decoder


def has_alpha(frame):
    return any(component.is_alpha for component in frame.format.components)


def decoded_frames(container, stream, decoder):
    for packet in container.demux(stream):
        throw_exception_if_processing_interrupted()
        for frame in decoder.decode(packet):
            if frame.is_corrupt:
                raise ValueError("Video contains a corrupt frame.")
            yield frame


def frame_time(frame, source_start):
    if frame.pts is None:
        raise ValueError("Video has no frame timestamps; frame_rate needs them (use 0).")
    return frame.pts * frame.time_base - source_start


def seek(container, stream, decoder, seconds):
    container.seek(max(0, math.floor(seconds / stream.time_base)), stream=stream, backward=True)
    decoder.flush_buffers()


def source_window(container, stream, decoder, start_time, source_rate, source_start):
    """Decoded frames from start_time on (seconds after the stream's start), jumping to the keyframe
    before it instead of decoding everything earlier."""
    tolerance = 0.5 / source_rate
    target = start_time - 2 / source_rate
    if target <= 0:
        return decoded_frames(container, stream, decoder)
    try:
        seek(container, stream, decoder, source_start + target)
    except av.error.FFmpegError:
        return decoded_frames(container, stream, decoder)  # Unseekable: nothing moved, decode from the top.
    frames = decoded_frames(container, stream, decoder)
    first = next(frames, None)
    if first is not None and frame_time(first, source_start) <= start_time - tolerance:
        return itertools.chain((first,), frames)
    # The seek landed after the start (inexact index). Decode from the beginning so no frame is missed.
    seek(container, stream, decoder, source_start)
    return decoded_frames(container, stream, decoder)


def timed_frames(container, stream, decoder, frame_rate, start_time=0, state=None, skip_output=0, origin=None):
    """Frames from start_time on (seconds after the stream's start), resampled to frame_rate if set.

    load_video uses this to skip frames by seeking, keeping exactly the frames that decoding from
    the top and skipping would keep. Native frames from start_time are frame n = start_time * fps.
    With a frame rate, the sampling grid stays anchored at ``origin`` (the first frame's time) and
    the first ``skip_output`` grid frames are dropped, since FFmpeg's resampler picks frames
    relative to that grid. The FPS conversion uses FFmpeg's timestamp resampler, so discarded
    frames are never converted to RGB.
    """
    state = {} if state is None else state
    source_rate = stream.average_rate or stream.guessed_rate
    source_start = (stream.start_time or 0) * stream.time_base
    frames = source_window(container, stream, decoder, start_time, source_rate, source_start) \
        if start_time > 0 else decoded_frames(container, stream, decoder)

    def remember(frame):
        state.setdefault("rotation", frame.rotation)
        state.setdefault("alpha", has_alpha(frame))
        return frame

    if not frame_rate:
        tolerance = 0.5 / source_rate
        for frame in frames:
            remember(frame)
            if start_time > 0 and frame_time(frame, source_start) < start_time - tolerance - 1e-9:
                continue
            yield frame
        return

    graph = None
    for frame in itertools.chain(frames, (None,)):
        throw_exception_if_processing_interrupted()
        if frame is not None:
            remember(frame)
            if frame.pts is None:
                raise ValueError("Video has no frame timestamps; use frame_rate = 0.")
        if graph is None:
            if frame is None:
                return
            graph = av.filter.Graph()
            # The grid starts at the first frame (STARTPTS), or at its known time when we have seeked.
            offset = "STARTPTS" if origin is None else repr(float((source_start + origin) / frame.time_base))
            graph.link_nodes(
                graph.add_buffer(width=frame.width, height=frame.height, format=frame.format,
                                 time_base=frame.time_base),
                graph.add("setpts", f"PTS-{offset}"),
                graph.add("fps", f"fps={Fraction(str(frame_rate))}:start_time=0:round=near"),
                graph.add("buffersink"),
            )
            graph.configure()
        graph.push(frame)
        while True:
            throw_exception_if_processing_interrupted()
            try:
                output = graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                break
            if skip_output and (output.pts is None or output.pts < skip_output):
                continue
            yield output


def probe_video(path):
    """Source facts for the node's info readout. Decodes one frame, for rotation and transparency."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key in _info_cache:
        return dict(_info_cache[key])
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("The selected file has no video stream.")
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.guessed_rate
        if rate is None or rate <= 0:
            raise ValueError("Could not determine the video's frame rate.")
        if stream.duration:
            seconds = float(stream.duration * stream.time_base)
        elif container.duration:
            seconds = container.duration / av.time_base
            if "matroska" in container.format.name and container.start_time:
                seconds -= container.start_time / av.time_base  # Matroska reports the end time.
            seconds = max(0.0, seconds)
        else:
            seconds = 0.0
        frames = stream.frames or round(seconds * rate)
        if not seconds:
            seconds = frames / rate
        decoder = alpha_decoder(stream)
        first = next(decoded_frames(container, stream, decoder), None)
        width, height, rotation, alpha, first_time = stream.width, stream.height, 0, False, None
        if first is not None:
            if first.pts is not None:
                first_time = float(first.pts * first.time_base - (stream.start_time or 0) * stream.time_base)
            width, height, rotation = first.width, first.height, first.rotation
            # GIF and some MOV codecs always decode with an alpha channel; report it only if it is used.
            alpha = has_alpha(first) and bool((first.to_ndarray(format="rgba")[..., 3] < 255).any())
        if rotation % 180:
            width, height = height, width
        info = {"width": width, "height": height, "rotation": rotation, "fps": float(rate),
                "duration": seconds, "frames": int(frames), "frames_exact": bool(stream.frames),
                # A real frame rate that differs from the average means variable frame timing.
                "variable": bool(stream.guessed_rate and abs(float(stream.guessed_rate) - float(rate)) > float(rate) / 1000),
                "audio": bool(container.streams.audio), "alpha": alpha, "first_time": first_time}
    if len(_info_cache) > 64:
        _info_cache.clear()
    _info_cache[key] = info
    return dict(info)


def load_audio_segment(path, start_time, duration):
    """Decode only the selected interval, keeping source channels and sample rate."""
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        sample_rate = stream.codec_context.sample_rate
        start_sample = round(start_time * sample_rate)
        sample_count = round(duration * sample_rate)
        waveform = np.zeros((stream.codec_context.channels, sample_count), dtype=np.float32)
        resampler = av.AudioResampler(format="fltp", layout=stream.codec_context.layout, rate=sample_rate)
        if start_time > (stream.start_time or 0) * stream.time_base:
            container.seek(int(start_time / stream.time_base), stream=stream, backward=True)
        for frame in itertools.chain(container.decode(stream), (None,)):
            throw_exception_if_processing_interrupted()
            if frame is not None and frame.is_corrupt:
                raise ValueError("Video contains corrupt audio.")
            for samples in resampler.resample(frame):
                if samples.pts is None:
                    raise ValueError("Audio has no timestamps; disable load_audio to load images only.")
                offset = round(samples.pts * samples.time_base * sample_rate) - start_sample
                if offset >= sample_count:
                    return {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": sample_rate}
                first, last = max(0, offset), min(sample_count, offset + samples.samples)
                if last > first:
                    waveform[:, first:last] = samples.to_ndarray()[:, first - offset:last - offset]
        return {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": sample_rate}


class WN_LoadVideo:
    CATEGORY = "WepeNerd/Video"
    FUNCTION = "load_video"
    RETURN_TYPES = ("IMAGE", "INT", "FLOAT", "AUDIO", "MASK")
    RETURN_NAMES = ("images", "frame_count", "frame_rate", "audio", "mask")
    OUTPUT_TOOLTIPS = (
        "RGB frames at the source resolution.",
        "Number of frames in images.",
        "Frame rate of images: the chosen (or source) FPS divided by select_every_nth.",
        "Audio for exactly the loaded clip, or None.",
        "Transparency as a mask (1 = transparent, like Load Image). A 64x64 empty mask if the video is opaque.",
    )
    DESCRIPTION = (
        "Load a video as RGB images. Order: frame rate, skip, every nth, cap, format trimming. "
        "Format presets (shared with Duration Selector) trim the frame count to a model's rule. "
        "The node shows what will load before you run it, and the preview loops that clip."
    )

    @classmethod
    def INPUT_TYPES(cls):
        # Only the video is required, so API prompts written for the previous version still run with
        # the new settings at their defaults. Optional inputs show as normal widgets in this order.
        return {"required": {
            "video": (list_videos(folder_paths.get_input_directory()), {"video_upload": True}),
        }, "optional": {
            "frame_load_cap": ("INT", {
                "default": DEFAULT_FRAME_CAP, "min": 0, "max": 2147483647,
                "tooltip": "Maximum frames to load, so long videos don't fill your RAM. 0 loads all.",
            }),
            "frame_rate": ("FLOAT", {
                "default": 0, "min": 0, "max": 240, "step": 0.01,
                "tooltip": "0 keeps every source frame. Otherwise sample at this FPS, duplicating or dropping frames as needed.",
            }),
            "skip_first_frames": ("INT", {
                "default": 0, "min": 0, "max": 2147483647,
                "tooltip": "Skip this many frames (after frame-rate conversion) before selecting every nth frame. Large skips jump straight there.",
            }),
            "select_every_nth": ("INT", {
                "default": 1, "min": 1, "max": 2147483647,
                "tooltip": "Keep every nth remaining frame. The output frame rate is divided by this value.",
            }),
            "format": (format_names(), {
                "default": "None",
                "tooltip": "Trim the frame count to a video model's rule (the Duration Selector presets). "
                           "Picking one also sets frame_rate to the model's FPS. None keeps the selected count.",
            }),
            "load_audio": ("BOOLEAN", {"default": True,
                "tooltip": "Output audio for the selected clip. Disable to skip audio decoding. Returns no audio if the file has no audio track."}),
        }}

    @classmethod
    def VALIDATE_INPUTS(cls, video, format="None"):
        try:
            video_path(video)
            resolve_format(format)
        except ValueError as error:
            return str(error)
        return True

    @classmethod
    def IS_CHANGED(cls, video, **kwargs):
        path = video_path(video)
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def load_video(self, video, frame_load_cap=DEFAULT_FRAME_CAP, frame_rate=0, skip_first_frames=0,
                   select_every_nth=1, format="None", load_audio=True):
        path = video_path(video)
        if not math.isfinite(frame_rate) or frame_rate < 0:
            raise ValueError("Frame rate must be finite and nonnegative.")
        if min(frame_load_cap, skip_first_frames) < 0 or select_every_nth < 1:
            raise ValueError("Cap and skip must be nonnegative; every nth must be at least 1.")
        preset = resolve_format(format)
        cap = frame_load_cap
        if cap:
            cap = largest_valid(cap, preset)
            if cap <= 0:
                raise ValueError(f"{format} needs a larger frame_load_cap. Increase the cap or use format None.")
        try:
            info = probe_video(path)
        except (ValueError, av.error.FFmpegError):
            info = None

        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError("The selected file has no video stream.")
            stream = container.streams.video[0]
            source_rate = stream.average_rate or stream.guessed_rate
            if source_rate is None or source_rate <= 0:
                raise ValueError("Could not determine the video's frame rate.")
            source_start = (stream.start_time or 0) * stream.time_base
            total = plan_clip(info, frame_rate, skip_first_frames, select_every_nth, frame_load_cap,
                              preset)["count"] if info else cap
            # Skip by seeking instead of decoding every skipped frame. With a frame rate the resampling
            # grid is known exactly. Native frame n sits at first_time + n / fps only when the timing is
            # even, which needs an exact frame count and matching real and average rates.
            start, skip, skip_output, origin = 0, skip_first_frames, 0, None
            first_time = info.get("first_time") if info else None
            if skip and first_time is not None:
                if frame_rate:
                    start = first_time + max(0, skip - 1) / float(Fraction(str(frame_rate)))
                    skip_output, origin, skip = skip_first_frames, first_time, 0
                elif info.get("frames_exact") and not info.get("variable"):
                    start = first_time + skip / float(Fraction(str(source_rate)))
                    skip = 0
            state = {}
            frames = itertools.islice(
                timed_frames(container, stream, alpha_decoder(stream), frame_rate, start, state,
                             skip_output=skip_output, origin=origin),
                skip, None, select_every_nth)
            if cap:
                frames = itertools.islice(frames, cap)
            first = next(frames, None)
            if first is None:
                raise ValueError("No frames selected. Lower skip_first_frames or choose another video.")
            if first.pts is None:
                audio_start = source_start + skip_first_frames / source_rate
            elif frame_rate:
                audio_start = first.pts * first.time_base + source_start + (origin or 0)
            else:
                audio_start = first.pts * first.time_base
            rotation, alpha = state["rotation"], state["alpha"]
            channels = 4 if alpha else 3
            width, height = first.width, first.height
            if rotation % 180:
                width, height = height, width
            progress = ProgressBar(total or 0)

            def pixels():
                for index, frame in enumerate(itertools.chain((first,), frames)):
                    throw_exception_if_processing_interrupted()
                    pixels = frame.to_ndarray(format="rgba" if alpha else "rgb24")
                    if rotation:
                        pixels = np.rot90(pixels, rotation // 90)
                    yield pixels
                    if total:
                        progress.update_absolute(index + 1)

            # Keep the decode buffer at one byte per channel, then allocate the final float batch once.
            images = np.fromiter(pixels(), dtype=np.dtype((np.uint8, (height, width, channels))))

        count = largest_valid(len(images), preset)
        if count <= 0:
            raise ValueError(f"Too few selected frames for {format}. Select more frames or use format None.")
        output = torch.from_numpy(images[:count, ..., :3]).to(dtype=torch.float32).div_(255).contiguous()
        mask = None
        if alpha:
            opacity = images[:count, ..., 3]
            if opacity.min() < 255:
                mask = torch.from_numpy(np.ascontiguousarray(opacity)).to(dtype=torch.float32).div_(-255).add_(1)
        if mask is None:
            mask = torch.zeros((1, 64, 64), dtype=torch.float32)
        output_rate = Fraction(str(frame_rate or source_rate)) / select_every_nth
        audio = load_audio_segment(path, audio_start, count / output_rate) if load_audio else None
        return (output, count, float(output_rate), audio, mask)


def register_load_video_routes():
    """Serve source facts so the node can show what it will load before the prompt runs."""
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    instance = PromptServer.instance
    if instance is None or getattr(instance, "_wn_load_video_routes_registered", False):
        return

    @instance.routes.get("/wepenerd/load_video/info")
    async def load_video_info(request):
        video = request.query.get("video", "")
        try:
            info = probe_video(video_path(video))
        except (OSError, ValueError, av.error.FFmpegError) as error:
            # 404 while the file isn't there yet: a dropped file's name is set before its upload ends.
            try:
                missing = not Path(folder_paths.get_annotated_filepath(video)).is_file()
            except (OSError, ValueError, TypeError):
                missing = True
            return web.json_response({"error": str(error)}, status=404 if missing else 400)
        return web.json_response(info)

    instance._wn_load_video_routes_registered = True


NODE_CLASS_MAPPINGS = {"WN_LoadVideo": WN_LoadVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_LoadVideo": "Load Video (Upload) (WepeNerd)"}
