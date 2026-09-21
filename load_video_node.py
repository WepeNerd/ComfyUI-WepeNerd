"""Load an uploaded video as a sampled RGB image batch."""

from fractions import Fraction
import itertools
import math
from pathlib import Path

import av
import numpy as np
import torch

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar


VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".gif"}
# Spatial divisor, temporal divisor, temporal remainder (VHS load presets).
LOAD_FORMATS = {
    "None": (1, 1, 0),
    "AnimateDiff": (8, 1, 0),
    "Mochi": (16, 6, 1),
    "LTXV": (32, 8, 1),
    "Hunyuan": (16, 4, 1),
    "Cosmos": (16, 8, 1),
    "Wan": (8, 4, 1),
    "H3": (32, 17, 5),
}


def video_path(video):
    root = Path(folder_paths.get_input_directory()).resolve()
    path = Path(folder_paths.get_annotated_filepath(video)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Video must be inside ComfyUI's input folder.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
        raise ValueError(f"Video file not found or unsupported: {video}")
    return path


def timed_frames(container, stream, frame_rate):
    """Use FFmpeg's timestamp resampler without converting discarded frames to RGB."""
    graph = None
    if frame_rate:
        graph = av.filter.Graph()
        graph.link_nodes(
            graph.add_buffer(template=stream),
            graph.add("setpts", "PTS-STARTPTS"),
            graph.add("fps", f"fps={Fraction(str(frame_rate))}:start_time=0:round=near"),
            graph.add("buffersink"),
        )
        graph.configure()
    for frame in itertools.chain(container.decode(stream), (None,)):
        throw_exception_if_processing_interrupted()
        if frame is not None and frame.is_corrupt:
            raise ValueError("Video contains a corrupt frame.")
        if graph is None:
            if frame is not None:
                yield frame
            continue
        if frame is not None and frame.pts is None:
            raise ValueError("Video has no frame timestamps; use frame_rate = 0.")
        graph.push(frame)
        while True:
            throw_exception_if_processing_interrupted()
            try:
                output = graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                break
            yield output


def output_size(width, height, custom_width, custom_height, divisor):
    if custom_width and custom_height:
        width, height = custom_width, custom_height
    elif custom_width:
        width, height = custom_width, height * custom_width / width
    elif custom_height:
        width, height = width * custom_height / height, custom_height
    return tuple(max(1, math.floor(size / divisor + 0.5)) * divisor for size in (width, height))


class WN_LoadVideo:
    CATEGORY = "WepeNerd/Video"
    FUNCTION = "load_video"
    RETURN_TYPES = ("IMAGE", "INT", "FLOAT")
    RETURN_NAMES = ("images", "frame_count", "frame_rate")
    DESCRIPTION = (
        "Load a video as RGB images. Order: frame rate, skip, every nth, cap, format trimming. "
        "Format presets round dimensions and trim frame counts to VHS model constraints. "
        "The frame rate remains under your control."
    )

    @classmethod
    def INPUT_TYPES(cls):
        root = Path(folder_paths.get_input_directory())
        files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*")
                       if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)
        return {"required": {
            "video": (files, {"video_upload": True}),
            "frame_rate": ("FLOAT", {
                "default": 0, "min": 0, "max": 240, "step": 0.01,
                "tooltip": "0 keeps every source frame. Otherwise sample at this FPS, duplicating or dropping frames as needed.",
            }),
            "frame_load_cap": ("INT", {
                "default": 0, "min": 0, "max": 2147483647,
                "tooltip": "Maximum selected frames to load. 0 loads all; large batches need more RAM.",
            }),
            "skip_first_frames": ("INT", {
                "default": 0, "min": 0, "max": 2147483647,
                "tooltip": "Skip this many frames AFTER frame-rate conversion, before selecting every nth frame.",
            }),
            "select_every_nth": ("INT", {
                "default": 1, "min": 1, "max": 2147483647,
                "tooltip": "Keep every nth remaining frame. The output frame rate is divided by this value.",
            }),
            "format": (list(LOAD_FORMATS), {
                "default": "None",
                "tooltip": "VHS dimension/frame-count constraints. May trim trailing frames. None preserves the selected count and dimensions.",
            }),
            "custom_width": ("INT", {"default": 0, "min": 0, "max": 16384,
                "tooltip": "0 uses source width, or preserves aspect ratio if only height is set."}),
            "custom_height": ("INT", {"default": 0, "min": 0, "max": 16384,
                "tooltip": "0 uses source height, or preserves aspect ratio if only width is set."}),
        }}

    @classmethod
    def VALIDATE_INPUTS(cls, video):
        try:
            video_path(video)
        except ValueError as error:
            return str(error)
        return True

    @classmethod
    def IS_CHANGED(cls, video, **kwargs):
        path = video_path(video)
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def load_video(self, video, frame_rate=0, frame_load_cap=0, skip_first_frames=0,
                   select_every_nth=1, format="None", custom_width=0, custom_height=0):
        path = video_path(video)
        if not math.isfinite(frame_rate) or frame_rate < 0:
            raise ValueError("Frame rate must be finite and nonnegative.")
        if min(frame_load_cap, skip_first_frames, custom_width, custom_height) < 0 or select_every_nth < 1:
            raise ValueError("Cap, skip and dimensions must be nonnegative; every nth must be at least 1.")
        if format not in LOAD_FORMATS:
            raise ValueError(f"Unknown video format preset: {format}")
        divisor, frame_divisor, remainder = LOAD_FORMATS[format]
        cap = frame_load_cap
        if cap:
            cap -= (cap - remainder) % frame_divisor
            if cap <= 0:
                raise ValueError(f"{format} needs at least {remainder or frame_divisor} selected frames. Increase the cap or use format None.")

        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError("The selected file has no video stream.")
            stream = container.streams.video[0]
            source_rate = stream.average_rate or stream.guessed_rate
            if source_rate is None or source_rate <= 0:
                raise ValueError("Could not determine the video's frame rate.")
            frames = itertools.islice(timed_frames(container, stream, frame_rate),
                                      skip_first_frames, None, select_every_nth)
            if cap:
                frames = itertools.islice(frames, cap)
            first = next(frames, None)
            if first is None:
                raise ValueError("No frames selected. Reduce skip_first_frames or choose another video.")
            rotation = first.rotation
            width, height = first.width, first.height
            if rotation % 180:
                width, height = height, width
            width, height = output_size(width, height, custom_width, custom_height, divisor)
            resize_width, resize_height = (height, width) if rotation % 180 else (width, height)
            progress = ProgressBar(cap or 0)

            def pixels():
                for index, frame in enumerate(itertools.chain((first,), frames)):
                    throw_exception_if_processing_interrupted()
                    rgb = frame.reformat(width=resize_width, height=resize_height,
                                         format="rgb24", interpolation="BICUBIC").to_ndarray()
                    if rotation:
                        rgb = np.rot90(rgb, rotation // 90)
                    yield rgb
                    if cap:
                        progress.update_absolute(index + 1)

            # Keep the decode buffer at one byte per channel, then allocate the final float batch once.
            images = np.fromiter(pixels(), dtype=np.dtype((np.uint8, (height, width, 3))))

        count = len(images) - (len(images) - remainder) % frame_divisor
        if count <= 0:
            raise ValueError(f"Too few selected frames for {format}. Select more frames or use format None.")
        output = torch.from_numpy(images[:count]).to(dtype=torch.float32).div_(255)
        return (output, count, float(frame_rate or source_rate) / select_every_nth)


NODE_CLASS_MAPPINGS = {"WN_LoadVideo": WN_LoadVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_LoadVideo": "Load Video (Upload) (WepeNerd)"}
