// H3 Ripple timeline rules (mirror of ripple_core.py). Pure functions, testable with node --test.

export const FPS = 24;
const CANVAS_MULTIPLE = 32;

// Short edge from a resolution value such as "384".
export function shortEdge(label) {
    const edge = parseInt(String(label), 10);
    return Number.isFinite(edge) && edge > 0 ? edge : 384;
}

// Same rule as ripple_core.canvas_size: the source aspect at this short edge, sides rounded to 32.
export function canvasSize(width, height, edge) {
    if (!(width > 0 && height > 0)) return null;
    const round = (value) => Math.max(CANVAS_MULTIPLE, Math.round(value / CANVAS_MULTIPLE) * CANVAS_MULTIPLE);
    return width >= height ? { width: round(edge * width / height), height: edge }
        : { width: edge, height: round(edge * height / width) };
}

// True when an edited image's aspect differs from the frame's by more than 1% (it would be centre-cropped).
export function aspectMismatch(imageWidth, imageHeight, canvas) {
    if (!canvas || !(imageWidth > 0 && imageHeight > 0)) return false;
    return Math.abs((imageWidth / imageHeight) / (canvas.width / canvas.height) - 1) > 0.01;
}

export function parseLength(label) {
    const frames = parseInt(String(label), 10);
    return Number.isFinite(frames) && frames >= 5 && frames % 17 === 5 ? frames : 107;
}

export function gridFrames(duration, firstTime = 0) {
    return Math.max(0, Math.floor(Math.max(0, duration - firstTime) * FPS + 0.5));
}

// Clamp a window into the source; never pads. Returns {start, length, edit}.
export function fitWindow(available, start, length, edit) {
    if (available >= 5 && length > available) length = 17 * Math.floor((available - 5) / 17) + 5;
    const maxStart = Math.max(0, available - length);
    start = Math.min(Math.max(0, Math.round(start)), maxStart);
    edit = Math.min(Math.max(0, Math.round(edit)), Math.max(0, length - 1));
    return { start, length, edit };
}

// Source time (seconds on the media timeline) at which the browser shows grid frame k. FFmpeg's
// fps=24 (round=near) filter keeps, for grid frame k, the last source frame that starts before the
// half-grid point (k + 0.5) / 24; a browser seeking just before that point shows the same frame.
export function seekTime(k, info) {
    const t = (info?.first_time ?? 0) + (k + 0.5) / FPS - 1e-4;
    const end = Math.max(0, (info?.duration ?? t) - 0.001);
    return Math.min(t, end);
}

// Grid frame shown at a media time (inverse of seekTime, for the playback playhead).
export function gridIndexAt(time, info) {
    return Math.max(0, Math.floor((time - (info?.first_time ?? 0)) * FPS + 1e-6));
}

export function timecode(seconds) {
    const s = Math.max(0, seconds);
    const minutes = Math.floor(s / 60);
    const rest = s - minutes * 60;
    return `${String(minutes).padStart(2, "0")}:${rest.toFixed(2).padStart(5, "0")}`;
}

export function describe(start, length, edit) {
    return `${timecode((start + edit) / FPS)} · frame ${edit + 1} of ${length} · ` +
        `window ${timecode(start / FPS)}–${timecode((start + length) / FPS)} (${(length / FPS).toFixed(2)} s)`;
}

// What a press at timeline x hits: the marker (within slop), the window body, or empty track.
export function hitTest(x, layout, start, length, edit, slop = 7) {
    const markerX = layout.x(start + edit + 0.5);
    if (Math.abs(x - markerX) <= slop) return "marker";
    if (x >= layout.x(start) && x <= layout.x(start + length)) return "window";
    return "track";
}

// Linear frame <-> pixel mapping for a track spanning [left, left + width] over `total` frames.
export function makeLayout(left, width, total) {
    const span = Math.max(1, total);
    return {
        left, width, total: span,
        x: (frame) => left + (frame / span) * width,
        frame: (x) => ((x - left) / width) * span,
    };
}

// Tick spacing in seconds that keeps labels about 60 px apart.
export function tickStep(totalSeconds, width) {
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const wanted = (totalSeconds / Math.max(1, width)) * 60;
    return steps.find((step) => step >= wanted) ?? 1200;
}

// ---- several edited-frame markers ----------------------------------------------------------

export const MAX_MARKERS = 4;

// Parse the node's markers JSON; falls back to one marker at editFrame. Sorted, unique, clamped.
export function parseMarkers(json, editFrame, length) {
    let items = [];
    try { items = json ? JSON.parse(json) : []; } catch (_) { items = []; }
    if (!Array.isArray(items) || !items.length) items = [{ frame: editFrame }];
    const out = []; const seen = new Set();
    for (const item of items) {
        const raw = typeof item === "object" && item ? item.frame : item;
        const frame = Math.min(Math.max(0, Math.round(Number(raw) || 0)), Math.max(0, length - 1));
        if (seen.has(frame)) continue;
        seen.add(frame);
        out.push({ frame, edited: (item && item.edited) || "", edited_at: item && Number.isFinite(item.edited_at) ? item.edited_at : null });
    }
    out.sort((a, b) => a.frame - b.frame);
    return out.slice(0, MAX_MARKERS);
}

// One marker on the window's first frame (ripple_core.default_markers).
export function defaultMarkers() {
    return [{ frame: 0, edited: "", edited_at: null }];
}

// After a length change: markers without an edit keep their relative place in the window; markers with an
// edit stay on their frame (so the edit still matches) unless it no longer fits.
export function rescaleMarkers(markers, oldLength, newLength) {
    if (!(oldLength > 1) || oldLength === newLength) return markers.map((m) => ({ ...m }));
    return markers.map((m) => ({
        ...m,
        frame: m.edited ? Math.min(m.frame, newLength - 1)
            : Math.min(newLength - 1, Math.round((m.frame * (newLength - 1)) / (oldLength - 1))),
    }));
}

// A free frame for a new marker: the preferred frame if free, else the nearest free one, else -1.
export function freeFrame(frames, preferred, length) {
    const taken = new Set(frames);
    for (let d = 0; d < length; d++) {
        for (const k of [preferred + d, preferred - d]) if (k >= 0 && k < length && !taken.has(k)) return k;
    }
    return -1;
}

// Where to put a new marker: the middle of the widest gap between existing markers / window edges.
export function suggestFrame(frames, length) {
    const points = [-1, ...[...frames].sort((a, b) => a - b), length];
    let best = 0, at = Math.floor(length / 2);
    for (let i = 1; i < points.length; i++) {
        const gap = points[i] - points[i - 1];
        if (gap > best) { best = gap; at = Math.floor((points[i] + points[i - 1]) / 2); }
    }
    return freeFrame(frames, Math.max(0, Math.min(length - 1, at)), length);
}

// Index of the marker under x (closest within slop), or -1.
export function hitMarker(x, layout, start, frames, slop = 7) {
    let best = -1, distance = slop + 1;
    frames.forEach((frame, index) => {
        const d = Math.abs(x - layout.x(start + frame + 0.5));
        if (d <= slop && d < distance) { best = index; distance = d; }
    });
    return best;
}

// Move one marker to `frame` without landing on another marker (stops next to it).
export function moveMarker(frames, index, frame, length) {
    const others = new Set(frames.filter((_, i) => i !== index));
    let target = Math.min(Math.max(0, Math.round(frame)), length - 1);
    if (!others.has(target)) return target;
    const step = target >= frames[index] ? -1 : 1;
    while (others.has(target) && target >= 0 && target < length) target += step;
    return target >= 0 && target < length ? target : frames[index];
}
