"""H3 Ripple: timeline, canvas and edited-frame checks shared by the nodes (no ComfyUI imports).

Everything lives on the 24 fps grid MiniMax H3 generates. ``window_start`` is a grid frame of
the source, ``length`` a valid H3 frame count (17k + 5) and ``edit_frame`` the frame inside the
window that the user edits (``j``, zero-based). js/ripple_timeline.mjs mirrors these rules.
"""

from __future__ import annotations

import json
import math

import numpy as np

FPS = 24
CANVAS_MULTIPLE = 32
# Window lengths offered (17k + 5 frames at 24 fps).
LENGTHS = (22, 39, 56, 73, 90, 107, 124, 141, 158, 175, 192)
DEFAULT_LENGTH = 107
# Short edge of the generation; the long edge follows the video's aspect.
RESOLUTIONS = {"352": 352, "384": 384, "544": 544, "768": 768}
DEFAULT_RESOLUTION = "384"
DEFAULT_PROMPT = (
    "Edit propagation: each edited frame shows exactly how that moment of the video should look after the edit. "
    "Apply the same changes to every frame of the clip, before, between and after the edited frames, so the edit "
    "stays consistent through the whole video. Keep the guide video's motion, timing, camera movement, composition "
    "and all unedited content unchanged."
)
# Edited frames per window: one by default; add more when an edit needs another anchor.
MAX_MARKERS = 4


def default_markers() -> list[dict]:
    """One marker on the window's first frame."""
    return [{"frame": 0, "edited": "", "edited_at": None}]


DEFAULT_MARKERS_JSON = json.dumps(default_markers(), separators=(",", ":"))


def short_edge(resolution) -> int:
    """Short edge from a resolution value; older labels such as "384 (...)" still read as 384."""
    if resolution in RESOLUTIONS:
        return RESOLUTIONS[resolution]
    try:
        return int(str(resolution).split()[0])
    except (ValueError, IndexError):
        return RESOLUTIONS[DEFAULT_RESOLUTION]


def length_label(frames: int) -> str:
    return f"{frames} frames ({frames / FPS:.2f} s)"


LENGTH_LABELS = [length_label(frames) for frames in LENGTHS]
DEFAULT_LENGTH_LABEL = length_label(DEFAULT_LENGTH)


def parse_length(label) -> int:
    """Frame count from a length label (or a plain number); must be 17k + 5."""
    frames = int(str(label).split()[0])
    if frames < 5 or frames % 17 != 5:
        raise ValueError(f"H3 windows need 17k + 5 frames, got {frames}")
    return frames


def grid_frame_count(duration: float, fps: float = FPS) -> int:
    """Frames on the 24 fps grid for a source of this duration (as FFmpeg's fps filter rounds)."""
    return max(0, math.floor(duration * fps + 0.5))


def fit_window(available: int, window_start: int, length: int, edit_frame: int) -> tuple[int, int, int, list[str]]:
    """Clamp a requested window into the source. Returns (start, length, edit_frame, notes).

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
    edit = min(max(0, int(edit_frame)), length - 1)
    if edit != edit_frame:
        notes.append(f"Edit frame moved to {edit} (inside the window).")
    return start, length, edit, notes


def parse_markers(markers, edit_frame: int, length: int) -> tuple[list[dict], list[str]]:
    """Edited-frame markers as a list of {"frame": j, "edited": input-folder path or "", "edited_at": k}, by frame.

    ``edited_at`` is the absolute source frame the attached edit was made for (None when unknown);
    see stale_markers.

    ``markers`` is the node's JSON widget (a list of objects or plain frame numbers). An empty value means
    a single marker at ``edit_frame`` (workflows saved before multiple markers existed). Frames are clamped
    into the window; duplicates keep the first marker; at most MAX_MARKERS are used.
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
        items = [{"frame": int(edit_frame)}]
    seen, out = set(), []
    for item in items:
        frame = int(item["frame"] if isinstance(item, dict) else item)
        clamped = min(max(0, frame), length - 1)
        if clamped != frame:
            notes.append(f"Edited frame {frame + 1} moved to {clamped + 1} (inside the window).")
        if clamped in seen:
            notes.append(f"Two markers on frame {clamped + 1}; keeping one.")
            continue
        seen.add(clamped)
        edited = str(item.get("edited") or "") if isinstance(item, dict) else ""
        edited_at = item.get("edited_at") if isinstance(item, dict) else None
        out.append({"frame": clamped, "edited": edited,
                    "edited_at": int(edited_at) if isinstance(edited_at, (int, float)) and edited else None})
    out.sort(key=lambda marker: marker["frame"])
    if len(out) > MAX_MARKERS:
        notes.append(f"Only the first {MAX_MARKERS} edited frames are used.")
        out = out[:MAX_MARKERS]
    return out, notes


def stale_markers(markers: list[dict], window_start: int) -> list[int]:
    """Markers whose attached edit was made for a different source frame (the marker or window moved since)."""
    return [m["frame"] for m in markers
            if m.get("edited") and m.get("edited_at") is not None and m["edited_at"] != window_start + m["frame"]]


def canvas_size(width: int, height: int, edge: int) -> tuple[int, int]:
    """Target canvas: the source aspect with the given short edge, each side a multiple of 32."""
    if width <= 0 or height <= 0:
        raise ValueError("Video size must be positive")
    if width >= height:
        long_edge = edge * width / height
        return max(CANVAS_MULTIPLE, round(long_edge / CANVAS_MULTIPLE) * CANVAS_MULTIPLE), edge
    long_edge = edge * height / width
    return edge, max(CANVAS_MULTIPLE, round(long_edge / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)


def timecode(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


def describe(start: int, length: int, edit) -> str:
    frames = [edit] if isinstance(edit, int) else list(edit)
    where = ", ".join(str(frame + 1) for frame in frames)
    label = "frame" if len(frames) == 1 else "frames"
    return (f"{timecode((start + frames[0]) / FPS)} · {label} {where} of {length} · window {timecode(start / FPS)}–"
            f"{timecode((start + length) / FPS)} ({length / FPS:.2f} s)")


# ---------------------------------------------------------------------------
# Edited-frame checks
# ---------------------------------------------------------------------------


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
    """A warning when the editor changed the image's aspect ratio (it will be centre-cropped back)."""
    (ew, eh), (cw, ch) = edited_size, canvas
    if abs((ew / eh) / (cw / ch) - 1) > 0.01:
        return (f"Edited image is {ew}x{eh} but the window is {cw}x{ch}; it was centre-cropped to fit, "
                "which can shift the edit. Keep the editor's output at the frame's aspect ratio.")
    return None
