// Ripple timeline and geometry rules shared with ripple_core.py.

export const FPS = 24;
// Native MiniMax H3 accepts at most 3600 frames; 3592 is the last complete 17k+5 choice.
export const MAX_DURATION_FRAMES = 3592;
export const DURATION_OPTIONS = Array.from({ length: (MAX_DURATION_FRAMES - 5) / 17 + 1 }, (_, index) => {
    const frames = 5 + index * 17;
    return [frames, `${frames} frames · ${(frames / FPS).toFixed(2)} s`];
});

// Notice substantial reshaping by an external editor.
export function aspectMismatch(imageWidth, imageHeight, canvas) {
    if (!canvas || !(imageWidth > 0 && imageHeight > 0)) return false;
    return Math.abs((imageWidth / imageHeight) / (canvas.width / canvas.height) - 1) > 0.05;
}

export function durationFrames(seconds) {
    const value = Number(seconds);
    return Number.isFinite(value) ? Math.max(5, 5 + 17 * Math.round((value * FPS - 5) / 17)) : 107;
}

// Resize a window around its opposite edge, leaving anchors on their source frames.
export function resizeWindow(available, start, length, side, frame) {
    const end = start + length;
    const limit = side === "left" ? end : available - start;
    const wanted = side === "left" ? end - frame : frame - start;
    const count = Math.min(MAX_DURATION_FRAMES, Math.max(5, durationFrames(wanted / FPS)), Math.max(5, 5 + 17 * Math.floor((limit - 5) / 17)));
    return { start: side === "left" ? end - count : start, length: count };
}

export function retainSourceFrames(markers, oldStart, newStart, length) {
    const used = new Set();
    const result = markers.map((marker) => ({ ...marker, frame: oldStart + marker.frame - newStart }));
    for (const marker of result) if (marker.frame >= 0 && marker.frame < length) used.add(marker.frame);
    for (const marker of result) {
        if (marker.frame >= 0 && marker.frame < length) continue;
        const frame = freeFrame([...used], Math.min(length - 1, Math.max(0, marker.frame)), length);
        marker.frame = frame >= 0 ? frame : Math.min(length - 1, Math.max(0, marker.frame));
        used.add(marker.frame);
    }
    return result;
}

export function parseCrop(value) {
    let crop = value;
    try { if (typeof crop === "string") crop = crop ? JSON.parse(crop) : [0, 0, 1, 1]; } catch (_) { crop = null; }
    if (!Array.isArray(crop) || crop.length !== 4 || crop.some((n) => !Number.isFinite(Number(n)))) return [0, 0, 1, 1];
    const x = Math.min(1, Math.max(0, Number(crop[0]))), y = Math.min(1, Math.max(0, Number(crop[1])));
    const width = Math.min(1 - x, Number(crop[2])), height = Math.min(1 - y, Number(crop[3]));
    if (width <= 0 || height <= 0) return [0, 0, 1, 1];
    return [x, y, width, height].map((v) => Number(v.toFixed(8)));
}

export function cropMatches(a, b) {
    const left = parseCrop(a), right = parseCrop(b);
    return left.every((value, i) => Math.abs(value - right[i]) < 1e-6);
}

export function cropForAspect(sourceAspect, targetAspect) {
    const ratio = targetAspect / sourceAspect;
    const width = Math.min(1, ratio), height = Math.min(1, 1 / ratio);
    return [(1 - width) / 2, (1 - height) / 2, width, height];
}

export function dragCrop(crop, handle, dx, dy, ratio = null) {
    const [x, y, width, height] = parseCrop(crop);
    if (handle === "move") return [Math.min(1 - width, Math.max(0, x + dx)), Math.min(1 - height, Math.max(0, y + dy)), width, height];
    const left = handle.includes("w"), top = handle.includes("n");
    let w = Math.min(left ? x + width : 1 - x, Math.max(0.02, width + (left ? -dx : dx)));
    let h = Math.min(top ? y + height : 1 - y, Math.max(0.02, height + (top ? -dy : dy)));
    if (ratio) {
        if (Math.abs(dx) >= Math.abs(dy * ratio)) h = w / ratio;
        else w = h * ratio;
        const scale = Math.min(1, (left ? x + width : 1 - x) / w, (top ? y + height : 1 - y) / h);
        w *= scale; h *= scale;
    }
    return [left ? x + width - w : x, top ? y + height - h : y, w, h];
}

// Same sizing as ripple_core: crop once, fit to a pixel budget, pad only for inference.
export function outputGeometry(width, height, settings = {}) {
    if (!(width > 0 && height > 0)) return null;
    const [x, y, w, h] = parseCrop(settings.crop);
    const x0 = Math.min(width - 1, Math.round(x * width)), y0 = Math.min(height - 1, Math.round(y * height));
    const cropWidth = Math.max(1, Math.min(width, Math.round((x + w) * width)) - x0);
    const cropHeight = Math.max(1, Math.min(height, Math.round((y + h) * height)) - y0);
    let scale = Number(settings.output_width) > 0 && Number(settings.output_height) > 0
        ? Math.min(Number(settings.output_width) / cropWidth, Number(settings.output_height) / cropHeight)
        : Math.sqrt(Number(settings.megapixels ?? 0.25) * 1e6 / (cropWidth * cropHeight));
    if (!settings.allow_upscale) scale = Math.min(1, scale);
    const resultWidth = Math.max(2, 2 * Math.floor(cropWidth * scale / 2)), resultHeight = Math.max(2, 2 * Math.floor(cropHeight * scale / 2));
    return { width: resultWidth, height: resultHeight,
        canvasWidth: Math.ceil(resultWidth / 32) * 32, canvasHeight: Math.ceil(resultHeight / 32) * 32,
        crop: [x0, y0, cropWidth, cropHeight] };
}

export function gridFrames(duration, firstTime = 0) {
    return Math.max(0, Math.floor(Math.max(0, duration - firstTime) * FPS + 0.5));
}

// Clamp a window into the source; never pads.
export function fitWindow(available, start, length) {
    if (available >= 5 && length > available) length = 17 * Math.floor((available - 5) / 17) + 5;
    const maxStart = Math.max(0, available - length);
    start = Math.min(Math.max(0, Math.round(start)), maxStart);
    return { start, length };
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

// Marker IDs stay stable while the batch remains in time order.
export function parseMarkers(json, length) {
    let items = [];
    try { items = json ? JSON.parse(json) : []; } catch (_) { items = []; }
    if (!Array.isArray(items) || !items.length) items = [{ id: 1, frame: 0 }];
    const out = []; const seen = new Set(); const ids = new Set();
    for (const item of items) {
        const raw = typeof item === "object" && item ? item.frame : item;
        let frame = Math.min(Math.max(0, Math.round(Number(raw) || 0)), Math.max(0, length - 1));
        if (seen.has(frame)) { const free = freeFrame([...seen], frame, length); if (free >= 0) frame = free; }
        seen.add(frame);
        let id = item?.id;
        if (!Number.isInteger(id) || id < 1 || ids.has(id)) { id = 1; while (ids.has(id)) id++; }
        ids.add(id);
        out.push({ id, frame, edited: (item && item.edited) || "", edited_at: item && Number.isFinite(item.edited_at) ? item.edited_at : null,
            ...(item?.edited_crop != null ? { edited_crop: parseCrop(item.edited_crop) } : {}),
            ...(item?.edited_source ? { edited_source: { video: String(item.edited_source.video ?? ""), version: String(item.edited_source.version ?? "") } } : {}) });
    }
    out.sort((a, b) => a.frame - b.frame);
    return out;
}

// One marker on the window's first frame (ripple_core.default_markers).
export function defaultMarkers() {
    return [{ id: 1, frame: 0, edited: "", edited_at: null }];
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
