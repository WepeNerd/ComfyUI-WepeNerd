"""Encode an IMAGE batch (plus optional audio and transparency) to one video file with PyAV."""

from fractions import Fraction
import json
import logging
import math
import os

import av
import numpy as np
import torch
from PIL import Image

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar

try:
    from comfy.cli_args import args as _cli_args
except ImportError:  # Standalone tests.
    _cli_args = None


log = logging.getLogger(__name__)
os.environ.setdefault("SVT_LOG", "2")  # SVT-AV1 prints its whole configuration at info level.

# Frames are converted on the batch's own device in blocks of about this many bytes, then encoded one by one.
BLOCK_BYTES = 64 << 20
BT709, MPEG_RANGE, JPEG_RANGE, SRGB_TRC, RGB_MATRIX = 1, 1, 2, 13, 0

# One entry per choice: the container, encoder and pixel formats (opaque, with alpha), how CRF maps onto the
# encoder and which audio codec the container takes. "hi" means frames go in as 16-bit RGB so 10/16-bit
# outputs keep the float precision instead of being rounded to 8 bits first.
FORMATS = {
    "MP4 · H.264": {"ext": "mp4", "mux": "mp4", "codec": "libx264", "pix": ("yuv420p", None),
                    "crf": 51, "audio": "aac", "options": {"preset": "medium"}},
    "MP4 · H.265 10-bit": {"ext": "mp4", "mux": "mp4", "codec": "libx265", "pix": ("yuv420p10le", None),
                           "crf": 51, "audio": "aac", "hi": True, "tag": "hvc1",
                           "options": {"preset": "medium", "x265-params": "log-level=error"}},
    "MP4 · AV1 10-bit": {"ext": "mp4", "mux": "mp4", "codec": "libsvtav1", "pix": ("yuv420p10le", None),
                         "crf": 63, "audio": "aac", "hi": True, "options": {"preset": "8"}},
    "WebM · VP9": {"ext": "webm", "mux": "webm", "codec": "libvpx-vp9", "pix": ("yuv420p", "yuva420p"),
                   "crf": 63, "audio": "libopus",
                   # Constant quality, multithreaded, at libvpx's recommended speed for file output.
                   "options": {"b": "0", "row-mt": "1", "deadline": "good", "cpu-used": "4"}},
    "MOV · ProRes (4444 with alpha)": {"ext": "mov", "mux": "mov", "codec": "prores_ks",
                                       "pix": ("yuv422p10le", "yuva444p10le"), "audio": "pcm_s16le", "hi": True},
    "MKV · FFV1 16-bit lossless": {"ext": "mkv", "mux": "matroska", "codec": "ffv1",
                                   "pix": ("gbrp16le", "gbrap16le"), "audio": "flac", "hi": True, "rgb": True,
                                   "options": {"level": "3", "slicecrc": "1"}},
    "GIF": {"ext": "gif", "codec": "gif", "alpha": True},
    "WebP": {"ext": "webp", "alpha": True},
}
DEFAULT_FORMAT = "MP4 · H.264"


def available_formats():
    return [name for name, spec in FORMATS.items() if "codec" not in spec or spec["codec"] in av.codecs_available]


def even_pad(pix_fmt, width, height):
    """Pixels to add (right, bottom) so chroma subsampling divides the frame size."""
    if pix_fmt.startswith(("yuv420", "yuva420")):
        return width % 2, height % 2
    if pix_fmt.startswith(("yuv422", "yuva422")):
        return width % 2, 0
    return 0, 0


def frame_rate(fps):
    """Exact rate for the encoder; 23.976, 29.97, 59.94... mean the NTSC rates n*1000/1001."""
    ntsc = round(fps * 1.001)
    if fps >= 1 and abs(fps - ntsc * 1000 / 1001) < 0.0015 and abs(fps - ntsc) > 0.0015:
        return Fraction(ntsc * 1000, 1001)
    return Fraction(str(round(fps, 6))).limit_denominator(1000)


def frame_order(count, pingpong):
    """Indices to write: forwards, then back again without repeating either end frame."""
    order = list(range(count))
    return order + order[-2:0:-1] if pingpong else order


def alpha_from(images, mask):
    """Opacity (1 = opaque) per frame as [frames|1, H, W], or None when the clip is opaque.

    `mask` follows Load Image / Load Video (1 = transparent). An empty mask whose size doesn't match, like
    the 64x64 placeholder those nodes output for opaque files, means "no transparency" rather than an error.
    """
    if mask is not None:
        mask = mask.reshape((-1, *mask.shape[-2:]))
        if tuple(mask.shape[-2:]) != tuple(images.shape[1:3]):
            if not mask.any():
                mask = None
            else:
                raise ValueError(f"mask is {mask.shape[-1]}x{mask.shape[-2]} but images are "
                                 f"{images.shape[2]}x{images.shape[1]}.")
        elif mask.shape[0] not in (1, images.shape[0]):
            raise ValueError(f"mask has {mask.shape[0]} frames; use 1 or one per image ({images.shape[0]}).")
    if mask is not None:
        return 1 - mask.to(device=images.device, dtype=torch.float32)
    if images.shape[-1] == 4:
        return images[..., 3]
    return None


def pixel_blocks(images, alpha, order, high_bit_depth, pad=(0, 0)):
    """Yield (H, W, 3|4) uint8 or uint16 frames in `order`, converting a block at a time on the batch's device.

    Values are rounded, not truncated, and only the selected frames ever leave the GPU.
    """
    peak = 65535 if high_bit_depth else 255
    channels = 3 if alpha is None else 4
    frame_bytes = images.shape[1] * images.shape[2] * channels * 4
    step = max(1, BLOCK_BYTES // frame_bytes)
    forward = order == list(range(len(order)))
    for start in range(0, len(order), step):
        throw_exception_if_processing_interrupted()
        if forward:
            picked = slice(start, start + step)
            rgb = images[picked, ..., :3]
            opacity = None if alpha is None else alpha[picked] if alpha.shape[0] > 1 else \
                alpha.expand(rgb.shape[0], -1, -1)
        else:
            index = torch.tensor(order[start:start + step], device=images.device)
            rgb = images.index_select(0, index)[..., :3]
            opacity = None if alpha is None else alpha.index_select(0, index) if alpha.shape[0] > 1 else \
                alpha.expand(rgb.shape[0], -1, -1)
        block = rgb if opacity is None else torch.cat((rgb, opacity.unsqueeze(-1).to(rgb.dtype)), dim=-1)
        block = block.to(torch.float32).mul(peak).add_(0.5).clamp_(0, peak)
        block = block.to(torch.int32 if high_bit_depth else torch.uint8).cpu().numpy()
        if high_bit_depth:
            block = block.astype(np.uint16)
        if any(pad):
            block = np.pad(block, ((0, 0), (0, pad[1]), (0, pad[0]), (0, 0)), mode="edge")
        yield from block


def workflow_metadata(prompt, extra_pnginfo):
    if _cli_args is not None and getattr(_cli_args, "disable_metadata", False):
        return {}
    metadata = {}
    if prompt is not None:
        metadata["prompt"] = prompt
    metadata.update(extra_pnginfo or {})
    return metadata


class AudioFeed:
    """Encode audio alongside the video so the two streams interleave, trimmed to the video's length."""

    LAYOUTS = {1: "mono", 2: "stereo", 6: "5.1"}

    def __init__(self, container, codec, audio, seconds):
        waveform = audio["waveform"]
        waveform = waveform.reshape((-1, *waveform.shape[-2:]))[0]
        if waveform.shape[0] not in self.LAYOUTS:
            waveform = waveform[:2]  # Formats differ on unusual layouts; keep the first two channels.
        self.rate = int(audio["sample_rate"])
        self.samples = waveform.to(device="cpu", dtype=torch.float32).numpy()
        self.total = min(self.samples.shape[1], round(seconds * self.rate))
        self.layout = self.LAYOUTS[self.samples.shape[0]]
        out_rate = 48000 if codec == "libopus" else self.rate
        self.stream = container.add_stream(codec, rate=out_rate, layout=self.layout)
        self.container, self.position = container, 0

    def until(self, seconds):
        end = self.total if seconds is None else min(self.total, round(seconds * self.rate))
        if end <= self.position:
            return
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(self.samples[:, self.position:end]),
                                           format="fltp", layout=self.layout)
        frame.sample_rate, frame.time_base, frame.pts = self.rate, Fraction(1, self.rate), self.position
        self.container.mux(self.stream.encode(frame))  # PyAV resamples and reframes for the encoder.
        self.position = end

    def finish(self):
        self.until(None)
        self.container.mux(self.stream.encode(None))


def encode_video(path, spec, frames, size, alpha, fps, crf, metadata, audio, frame_count, progress):
    width, height = size
    pix_fmt = spec["pix"][1 if alpha else 0]
    options = {"movflags": "use_metadata_tags+faststart"} if spec["mux"] in ("mp4", "mov") else {}
    with av.open(path, "w", format=spec["mux"], options=options) as container:
        for key, value in metadata.items():
            container.metadata[key] = json.dumps(value)
        stream = container.add_stream(spec["codec"], rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, pix_fmt
        stream.thread_type = "AUTO"
        context = stream.codec_context
        stream.options = encoder_options(spec, alpha, crf)
        if spec.get("tag"):
            context.codec_tag = spec["tag"]  # hvc1 lets QuickTime and Safari play H.265.
        rgb = spec.get("rgb", False)
        context.color_primaries = BT709
        # YUV deliveries are tagged BT.709 throughout, which is what players and video sites assume, so
        # colours match ComfyUI's preview. RGB (FFV1) keeps the pixels as they are and says so.
        context.color_trc = SRGB_TRC if rgb else BT709
        context.colorspace = RGB_MATRIX if rgb else BT709
        context.color_range = JPEG_RANGE if rgb else MPEG_RANGE
        feed = AudioFeed(container, spec["audio"], audio, frame_count / fps) if audio else None
        in_format = ("rgba64le" if alpha else "rgb48le") if spec.get("hi") else ("rgba" if alpha else "rgb24")
        for index, pixels in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(pixels, format=in_format)
            frame = frame.reformat(format=pix_fmt, dst_colorspace=None if rgb else BT709,
                                   dst_color_range=None if rgb else MPEG_RANGE)
            frame.pts = index
            frame.time_base = 1 / fps
            container.mux(stream.encode(frame))
            if feed:
                feed.until((index + 1) / fps)
            progress.update_absolute(index + 1)
        container.mux(stream.encode(None))
        if feed:
            feed.finish()


def encoder_options(spec, alpha, crf):
    options = dict(spec.get("options", {}))
    codec = spec["codec"]
    if codec == "prores_ks":
        options["profile"] = "4" if alpha else "3"  # 4444, or 422 HQ.
    elif codec == "libsvtav1" and crf == 0:
        options["svtav1-params"] = "lossless=1"
    elif codec == "libvpx-vp9" and crf == 0:
        options["lossless"] = "1"
    elif "crf" in spec:
        options["crf"] = str(min(crf, spec["crf"]))
    return options


def encode_gif(path, frames, size, alpha, fps, progress):
    """One pass: palettegen and paletteuse share the stream, so frames are only converted once."""
    width, height = size
    in_format = "rgba" if alpha else "rgb24"
    time_base = 1 / fps
    with av.open(path, "w", format="gif", options={"loop": "0"}) as container:
        stream = container.add_stream("gif", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "pal8"
        graph = av.filter.Graph()
        source = graph.add_buffer(width=width, height=height, format=in_format, time_base=time_base)
        split, sink = graph.add("split"), graph.add("buffersink")
        palette = graph.add("palettegen", f"reserve_transparent={int(alpha)}")
        apply = graph.add("paletteuse", "dither=sierra2_4a:diff_mode=rectangle:alpha_threshold=128")
        source.link_to(split)
        split.link_to(apply, 0, 0)
        split.link_to(palette, 1, 0)
        palette.link_to(apply, 0, 1)
        apply.link_to(sink)
        graph.configure()

        def drain():
            while True:
                try:
                    output = graph.pull()
                except (av.error.BlockingIOError, av.error.EOFError):
                    return
                container.mux(stream.encode(output))

        for index, pixels in enumerate(frames):
            frame = av.VideoFrame.from_ndarray(pixels, format=in_format)
            frame.pts, frame.time_base = index, time_base
            graph.push(frame)
            progress.update_absolute(index + 1)
        graph.push(None)  # palettegen needs every frame before the first one can be written.
        drain()
        container.mux(stream.encode(None))


def encode_webp(path, frames, alpha, fps, crf, metadata, progress):
    images = []
    for index, pixels in enumerate(frames):
        images.append(Image.fromarray(pixels, "RGBA" if alpha else "RGB"))
        progress.update_absolute(index + 1)
    exif = Image.Exif()
    # Same tags as ComfyUI's Save Animated WEBP, so the workflow loads when the file is dropped on the canvas.
    if "prompt" in metadata:
        exif[0x0110] = "prompt:" + json.dumps(metadata["prompt"])
    tag = 0x010F
    for key, value in metadata.items():
        if key != "prompt":
            exif[tag] = f"{key}:{json.dumps(value)}"
            tag -= 1
    images[0].save(path, format="WEBP", save_all=True, append_images=images[1:], duration=1000 / float(fps),
                   loop=0, lossless=crf == 0, quality=100 if crf == 0 else max(0, 100 - crf), method=4,
                   exif=exif)


class WN_SaveVideo:
    CATEGORY = "WepeNerd/Video"
    FUNCTION = "save_video"
    OUTPUT_NODE = True
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filepath",)
    OUTPUT_TOOLTIPS = ("Full path of the saved file.",)
    DESCRIPTION = (
        "Save images as one video file, with audio and transparency, in a single pass. "
        "No FFmpeg executable or sidecar files; the workflow is embedded in the video."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE", {"tooltip": "Frames to save. A fourth channel is used as transparency."}),
            "fps": ("FLOAT", {"default": 24.0, "min": 0.01, "max": 240.0, "step": 0.01,
                              "tooltip": "Frame rate. 23.976, 29.97 and 59.94 are saved as the exact NTSC rates (24000/1001...)."}),
            "filename_prefix": ("STRING", {"default": "video",
                                           "tooltip": "Name prefix; may include subfolders and %date:...% tokens."}),
            "format": (available_formats(), {
                "default": DEFAULT_FORMAT,
                "tooltip": "MP4 plays everywhere. WebM, ProRes, FFV1, GIF and WebP keep transparency. "
                           "FFV1 is a lossless 16-bit archive; ProRes is for editing software.",
            }),
            "crf": ("INT", {"default": 19, "min": 0, "max": 63, "step": 1,
                            "tooltip": "Quality: lower is better and larger. 0 is lossless for AV1, VP9 and WebP. "
                                       "H.264/H.265 stop at 51. Ignored by ProRes, FFV1 and GIF."}),
            "pingpong": ("BOOLEAN", {"default": False,
                                     "tooltip": "Play forwards then backwards, for a seamless loop."}),
            "save_output": ("BOOLEAN", {"default": True,
                                        "tooltip": "Save to the output folder. Off writes a temporary preview only."}),
        }, "optional": {
            "audio": ("AUDIO", {"tooltip": "Muxed into the same file and trimmed to the video's length. "
                                           "Not supported by GIF and WebP."}),
            "mask": ("MASK", {"tooltip": "Transparency, 1 = transparent (as output by Load Image and Load Video). "
                                         "One mask for all frames or one per frame."}),
        }, "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}}

    def save_video(self, images, fps, filename_prefix, format=DEFAULT_FORMAT, crf=19, pingpong=False,
                   save_output=True, audio=None, mask=None, prompt=None, extra_pnginfo=None):
        if format not in FORMATS:
            raise ValueError(f"Unknown format: {format}")
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be a positive number.")
        if images.ndim != 4 or images.shape[0] == 0:
            raise ValueError("images must be a non-empty IMAGE batch.")
        spec = FORMATS[format]
        rate = frame_rate(fps)
        alpha = alpha_from(images, mask)
        transparent = alpha is not None and spec.get("alpha", bool(spec.get("pix", (None, None))[1]))
        if alpha is not None and not transparent:
            log.warning("Save Video: %s has no transparency; saving opaque frames.", format)
        alpha = alpha if transparent else None
        if audio is not None and "audio" not in spec:
            log.warning("Save Video: %s can't hold audio; saving without it.", format)
            audio = None
        if audio is not None and audio.get("waveform") is None:
            audio = None

        height, width = images.shape[1:3]
        pad = even_pad(spec["pix"][1 if alpha is not None else 0], width, height) if "pix" in spec else (0, 0)
        if any(pad):
            log.warning("Save Video: %dx%d isn't allowed by %s; repeated the edge pixels to %dx%d.",
                        width, height, format, width + pad[0], height + pad[1])
        order = frame_order(images.shape[0], pingpong)

        directory = folder_paths.get_output_directory() if save_output else folder_paths.get_temp_directory()
        folder, name, counter, subfolder, _ = folder_paths.get_save_image_path(filename_prefix, directory,
                                                                               width, height)
        filename = f"{name}_{counter:05}_.{spec['ext']}"
        path = os.path.join(folder, filename)
        metadata = workflow_metadata(prompt, extra_pnginfo)
        frames = pixel_blocks(images, alpha, order, spec.get("hi", False), pad)
        progress = ProgressBar(len(order))
        try:
            if format == "GIF":
                encode_gif(path, frames, (width, height), alpha is not None, rate, progress)
            elif format == "WebP":
                encode_webp(path, frames, alpha is not None, rate, crf, metadata, progress)
            else:
                encode_video(path, spec, frames, (width + pad[0], height + pad[1]), alpha is not None, rate, crf,
                             metadata, audio, len(order), progress)
        except BaseException:
            # Don't leave a truncated file that the next run's counter skips past (also on Cancel).
            if os.path.exists(path):
                os.remove(path)
            raise
        preview = {"filename": filename, "subfolder": subfolder, "type": "output" if save_output else "temp"}
        return {"ui": {"images": [preview], "animated": (True,)}, "result": (path,)}


NODE_CLASS_MAPPINGS = {"WN_SaveVideo": WN_SaveVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_SaveVideo": "Save Video (WepeNerd)"}
