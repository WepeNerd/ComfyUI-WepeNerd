"""Frame Number Selector: pick a model-legal frame count from a preset-filtered list.

The ``frames`` dropdown lists plain frame numbers. It is declared with every
preset's numbers combined, so ComfyUI's validation always accepts the value;
js/wn_frame_number.js narrows the visible list to the selected preset. A value that isn't legal for the chosen
preset (old workflow, API call, extension not loaded) is snapped to the nearest
legal count, ties going up.
"""

import json
import logging
import re
from pathlib import Path

PRESETS_PATH = Path(__file__).resolve().parent / "frame_number_presets.json"
_LEADING_INT = re.compile(r"^\s*(\d+)")
log = logging.getLogger("WepeNerd")


def make_label(frames):
    """Dropdown entry: just the frame number. Seconds come out of the `seconds` output."""
    return str(int(frames))


def parse_frames(value):
    if isinstance(value, (int, float)):
        return int(value)
    match = _LEADING_INT.match(str(value))
    if not match:
        raise ValueError(f"Can't read a frame count from {value!r}.")
    return int(match.group(1))


def nearest(frames, target):
    return min(frames, key=lambda f: (abs(f - target), -f))


def expand_preset(cfg):
    fps = float(cfg.get("fps", 24))
    if fps <= 0:
        raise ValueError("Preset fps must be greater than zero.")
    if "frames" in cfg:
        frames = sorted({int(f) for f in cfg["frames"] if int(f) > 0})
    else:
        step, offset = int(cfg["step"]), int(cfg.get("offset", 0))
        if step <= 0:
            raise ValueError("Preset step must be greater than zero.")
        low, high = int(cfg.get("min", offset)), int(cfg["max"])
        frames = [f for f in range(offset, high + 1, step) if f >= max(low, 1)]
    return fps, frames


def load_presets(path=PRESETS_PATH):
    """Read the presets file; re-read on every call so edits apply after a browser refresh."""
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    presets = {}
    for name, cfg in raw.items():
        if name.startswith("_") or not isinstance(cfg, dict):
            continue
        try:
            fps, frames = expand_preset(cfg)
        except (KeyError, TypeError, ValueError) as error:
            log.warning("[WepeNerd] Frame Number Selector: skipping preset %r (%s).", name, error)
            continue
        if not frames:
            continue
        default = nearest(frames, int(cfg.get("default", frames[0])))
        presets[name] = {
            "fps": fps,
            "frames": frames,
            "labels": [make_label(f) for f in frames],
            "default": make_label(default),
            # Load Video trims to this rule, which (unlike the list) is not cut off at min/max.
            "rule": None if "frames" in cfg else (int(cfg["step"]), int(cfg.get("offset", 0))),
        }
    if not presets:
        raise ValueError(f"No usable presets in {path}.")
    return presets


def union_labels(presets):
    """Every preset's frame numbers, each listed once, in ascending order."""
    frames = sorted({f for preset in presets.values() for f in preset["frames"]})
    return [make_label(f) for f in frames]


def select_frames(presets, preset, frames):
    if preset not in presets:
        raise ValueError(f"Unknown preset {preset!r}. Available: {', '.join(presets)}.")
    entry = presets[preset]
    count = parse_frames(frames)
    if count not in entry["frames"]:
        snapped = nearest(entry["frames"], count)
        log.warning("[WepeNerd] Frame Number Selector: %d frames isn't valid for %s; using %d.",
                    count, preset, snapped)
        count = snapped
    return count, entry["fps"], round(count / entry["fps"], 4)


class WN_FrameNumberSelector:
    """Preset-filtered dropdown of legal frame counts for video models."""

    CATEGORY = "WepeNerd/Video"
    FUNCTION = "select"
    RETURN_TYPES = ("INT", "FLOAT", "FLOAT")
    RETURN_NAMES = ("frames", "fps", "seconds")
    OUTPUT_TOOLTIPS = (
        "Frame count. Connect to length, num_frames, frame_count or batch_size inputs.",
        "The preset's native frame rate.",
        "Clip duration in seconds: frames / fps.",
    )
    DESCRIPTION = (
        "Pick a frame count that is legal for a video model. The preset filters the list "
        "to that model's grid (8n+1 for LTX, 17n+5 for MiniMax H3, 4n+1 for Wan and "
        "Hunyuan, 6n+1 for Mochi). Edit frame_number_presets.json to add presets."
    )

    @classmethod
    def INPUT_TYPES(cls):
        presets = load_presets()
        names = list(presets)
        return {
            "required": {
                "preset": (names, {
                    "default": names[0],
                    "tooltip": "Video model whose frame-count rule the list follows.",
                }),
                "frames": (union_labels(presets), {
                    "default": presets[names[0]]["default"],
                    "tooltip": "Valid frame counts for the selected preset. "
                               "Changing preset keeps the nearest valid count.",
                }),
            }
        }

    def select(self, preset, frames):
        return select_frames(load_presets(), preset, frames)


def register_frame_number_routes():
    """Serve the expanded presets so the frontend filter matches the backend exactly."""
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    instance = PromptServer.instance
    if instance is None or getattr(instance, "_wn_frame_number_routes_registered", False):
        return

    @instance.routes.get("/wepenerd/frame_number/presets")
    async def frame_number_presets(_request):
        try:
            data = {name: {"labels": p["labels"], "default": p["default"], "fps": p["fps"],
                           "rule": p["rule"], "min": p["frames"][0], "max": p["frames"][-1],
                           **({} if p["rule"] else {"frames": p["frames"]})}
                    for name, p in load_presets().items()}
        except (OSError, ValueError) as error:
            return web.json_response({"error": str(error)}, status=500)
        return web.json_response(data)

    instance._wn_frame_number_routes_registered = True


NODE_CLASS_MAPPINGS = {"WN_FrameNumberSelector": WN_FrameNumberSelector}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_FrameNumberSelector": "Frame Number Selector (WepeNerd)"}
