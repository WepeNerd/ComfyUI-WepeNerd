"""Shared Ripple timeline, canvas geometry and edited-frame validation.

Positions use the 24 fps grid; window lengths follow H3's 17k + 5 frame rule.
js/ripple_timeline.mjs mirrors these rules.
"""

from __future__ import annotations

import json
import math

import numpy as np

FPS = 24
CANVAS_MULTIPLE = 32
DEFAULT_DURATION = 107 / FPS
DEFAULT_MEGAPIXELS = 0.25
DEFAULT_PROMPT = (
    "Edit propagation: each edited frame shows exactly how that moment of the video should look after the edit. "
    "Apply the same changes to every frame of the clip, before, between and after the edited frames, so the edit "
    "stays consistent through the whole video. Keep the guide video's motion, timing, camera movement, composition "
    "and all unedited content unchanged."
)
RESIZE_METHODS = ("lanczos", "bicubic", "bilinear", "nearest-exact", "area")


def default_markers() -> list[dict]:
    """One marker on the window's first frame."""
    return [{"id": 1, "frame": 0, "edited": "", "edited_at": None}]


DEFAULT_MARKERS_JSON = json.dumps(default_markers(), separators=(",", ":"))


def duration_frames(duration: float) -> int:
    if not math.isfinite(float(duration)) or duration <= 0:
        raise ValueError("Duration must be a finite positive number of seconds.")
    return max(5, 5 + 17 * math.floor((duration * FPS - 5) / 17 + 0.5))


def normalized_crop(crop="") -> list[float]:
    if crop is None or crop == "":
        return [0.0, 0.0, 1.0, 1.0]
    values = json.loads(crop) if isinstance(crop, str) else crop
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError("Crop must be [x, y, width, height] in normalized source coordinates.")
    x, y, w, h = map(float, values)
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise ValueError("Crop must have finite coordinates and positive width and height.")
    x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)
    if w <= 0 or h <= 0:
        raise ValueError("Crop must overlap the source image.")
    return [round(v, 8) for v in (x, y, w, h)]


def geometry(width: int, height: int, megapixels=DEFAULT_MEGAPIXELS, crop="", allow_upscale=False,
             resize_method="lanczos", output_width=0, output_height=0) -> dict:
    """Source crop, visible content and aligned model canvas."""
    if width < 1 or height < 1:
        raise ValueError("Video size must be positive.")
    if resize_method not in RESIZE_METHODS:
        raise ValueError(f"Unknown resize method: {resize_method}")
    mp = float(megapixels)
    if not math.isfinite(mp) or mp <= 0 or output_width < 0 or output_height < 0:
        raise ValueError("Megapixels must be positive; fit width and height must be zero or positive.")
    if bool(output_width) != bool(output_height):
        raise ValueError("Set both fit width and fit height, or leave both at zero.")
    selected_crop = normalized_crop(crop)
    x, y, w, h = selected_crop
    left = min(width - 1, math.floor(x * width + 0.5))
    top = min(height - 1, math.floor(y * height + 0.5))
    right = min(width, max(left + 1, math.floor((x + w) * width + 0.5)))
    bottom = min(height, max(top + 1, math.floor((y + h) * height + 0.5)))
    sw, sh = right - left, bottom - top
    scale = math.sqrt(mp * 1_000_000 / (sw * sh)) if mp else 1.0
    if output_width and output_height:
        scale = min(output_width / sw, output_height / sh)
    if not allow_upscale:
        scale = min(1.0, scale)
    cw, ch = max(2, 2 * math.floor(sw * scale / 2)), max(2, 2 * math.floor(sh * scale / 2))
    canvas_w, canvas_h = math.ceil(cw / 32) * 32, math.ceil(ch / 32) * 32
    return {"crop": selected_crop, "source_rect": [left, top, sw, sh],
            "width": canvas_w, "height": canvas_h,
            "content_rect": [(canvas_w - cw) // 2, (canvas_h - ch) // 2, cw, ch],
            "resize_method": resize_method}


def grid_frame_count(duration: float, fps: float = FPS) -> int:
    """Frames on the 24 fps grid for a source of this duration (as FFmpeg's fps filter rounds)."""
    return max(0, math.floor(duration * fps + 0.5))


def fit_window(available: int, window_start: int, length: int) -> tuple[int, int, list[str]]:
    """Clamp a requested window into the source. Returns (start, length, notes).

    The window keeps its length when it fits and slides back from the end of the video; a source
    shorter than the window gets the longest valid length that fits. Never pads or loops frames.
    """
    notes = []
    if available < 5:
        raise ValueError(f"The video has only {available} frames at 24 fps; H3 needs at least 5.")
    if length > available:
        shorter = 17 * ((available - 5) // 17) + 5
        notes.append(f"Video is shorter than {length} frames; using {shorter}.")
        length = shorter
    start = min(max(0, int(window_start)), available - length)
    if start != window_start:
        notes.append(f"Window moved to start at frame {start} so it fits the video.")
    return start, length, notes


def parse_markers(markers, length: int) -> tuple[list[dict], list[str]]:
    """Edited-frame markers as a list of {"frame": j, "edited": input-folder path or "", "edited_at": k}, by frame.

    ``edited_at`` is the absolute source frame the attached edit was made for (None when unknown);
    see stale_markers.

    ``markers`` is the node's JSON widget (a list of objects). An empty value means
    a single marker at the first frame. Frames are clamped
    into the window; colliding markers move to the nearest free frame without losing their identity.
    """
    notes = []
    items = []
    if isinstance(markers, str) and markers.strip():
        try:
            items = json.loads(markers)
        except json.JSONDecodeError as error:
            raise ValueError(f"Edited-frame markers are not valid JSON: {error}") from error
    elif isinstance(markers, list):
        items = markers
    if not items:
        items = default_markers()
    seen, ids, out = set(), set(), []
    for item in items:
        frame = int(item["frame"] if isinstance(item, dict) else item)
        clamped = min(max(0, frame), length - 1)
        if clamped != frame:
            notes.append(f"Edited frame {frame + 1} moved to {clamped + 1} (inside the window).")
        if clamped in seen:
            replacement = next((k for d in range(1, length) for k in (clamped + d, clamped - d)
                                if 0 <= k < length and k not in seen), clamped)
            if replacement != clamped:
                notes.append(f"Edited frame {clamped + 1} moved to {replacement + 1} to keep both markers.")
                clamped = replacement
        seen.add(clamped)
        identity = item.get("id") if isinstance(item, dict) else None
        if not isinstance(identity, int) or isinstance(identity, bool) or identity < 1 or identity in ids:
            identity = next(i for i in range(1, len(items) + 2) if i not in ids)
        ids.add(identity)
        edited = str(item.get("edited") or "") if isinstance(item, dict) else ""
        edited_at = item.get("edited_at") if isinstance(item, dict) else None
        entry = {"id": identity, "frame": clamped, "edited": edited,
                 "edited_at": int(edited_at) if isinstance(edited_at, (int, float)) and edited else None}
        if isinstance(item, dict) and item.get("edited_crop") is not None:
            entry["edited_crop"] = normalized_crop(item["edited_crop"])
        if isinstance(item, dict) and isinstance(item.get("edited_source"), dict):
            entry["edited_source"] = {key: str(item["edited_source"].get(key, "")) for key in ("video", "version")}
        out.append(entry)
    out.sort(key=lambda marker: marker["frame"])
    return out, notes


def stale_markers(markers: list[dict], window_start: int, crop=None, source=None, version=None) -> list[int]:
    """Markers whose attached edit was made for a different source frame (the marker or window moved since)."""
    return [m["frame"] for m in markers if m.get("edited") and
            ((m.get("edited_at") is not None and m["edited_at"] != window_start + m["frame"])
             or (crop is not None and m.get("edited_crop") is not None
                 and normalized_crop(m["edited_crop"]) != normalized_crop(crop))
             or (source is not None and m.get("edited_source") is not None
                 and (m["edited_source"].get("video") != source
                      or (version is not None and m["edited_source"].get("version") != version))))]


def timecode(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


def describe(start: int, length: int, edit) -> str:
    frames = [edit] if isinstance(edit, int) else list(edit)
    where = ", ".join(str(frame + 1) for frame in frames)
    label = "frame" if len(frames) == 1 else "frames"
    return (f"{timecode((start + frames[0]) / FPS)} · {label} {where} of {length} · window {timecode(start / FPS)}–"
            f"{timecode((start + length) / FPS)} ({length / FPS:.2f} s)")


def _structure(images: np.ndarray, size: int = 48) -> np.ndarray:
    """Gradient-magnitude maps of grey thumbnails, one row per image: layout, not colour."""
    images = np.asarray(images, dtype=np.float32)
    if images.ndim == 3:
        images = images[None]
    count, height, width = images.shape[:3]
    grey = images[..., :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    ys = (np.arange(size) * height // size).clip(0, height - 1)
    xs = (np.arange(size) * width // size).clip(0, width - 1)
    # Box-average blocks before sampling so small details and noise do not dominate.
    small = np.stack([
        np.add.reduceat(np.add.reduceat(frame, np.r_[ys, height][:-1], axis=0), np.r_[xs, width][:-1], axis=1)
        for frame in grey
    ])
    gy = np.abs(np.diff(small, axis=1))[:, :, :-1]
    gx = np.abs(np.diff(small, axis=2))[:, :-1, :]
    maps = (gx + gy).reshape(count, -1)
    maps -= maps.mean(axis=1, keepdims=True)
    maps /= np.linalg.norm(maps, axis=1, keepdims=True) + 1e-6
    return maps


def match_edited_frame(window: np.ndarray, edited: np.ndarray, edit_frame: int) -> dict:
    """Which window frame the edited image was most likely made from.

    ``window`` [N, H, W, 3] and ``edited`` [H, W, 3] in [0, 1] at the same size. Compares layout
    (edges), so colour and style edits still match their source frame. Returns the correlation at
    ``edit_frame``, the best-matching frame and a verdict: "ok", "mismatch" (another frame matches
    clearly better: the image was probably taken from a different position) or "unclear"
    (big structural edit or static footage; nothing to conclude).
    """
    maps = _structure(window)
    target = _structure(edited)[0]
    scores = maps @ target
    best = int(scores.argmax())
    at_edit = float(scores[edit_frame])
    spread = float(scores.max() - np.median(scores))
    if abs(best - edit_frame) <= 2 or scores[best] - at_edit < 0.05:
        verdict = "ok"
    elif scores[best] > 0.5 and spread > 0.08:
        verdict = "mismatch"
    else:
        verdict = "unclear"
    identical = bool(np.abs(np.asarray(window[edit_frame], np.float32) - np.asarray(edited, np.float32)).mean() < 1 / 255)
    return {"score": at_edit, "best": best, "best_score": float(scores[best]), "verdict": verdict, "unchanged": identical}


def aspect_note(edited_size: tuple[int, int], canvas: tuple[int, int]) -> str | None:
    """Report substantial editor reshaping; small latent-grid rounding is expected."""
    (ew, eh), (cw, ch) = edited_size, canvas
    if abs((ew / eh) / (cw / ch) - 1) > 0.05:
        return (f"Edited image is {ew}x{eh}; the complete image was resized to the frame's {cw}x{ch} content area.")
    return None
