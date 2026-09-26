const TILE = 128;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const number = (value, fallback, low, high) => Number.isFinite(Number(value)) ? clamp(Number(value), low, high) : fallback;

export function maskSettings(value = {}, maskOnly = false) {
    if (!value || typeof value !== "object") value = {};
    return { ...value, v: 1, open: value.open ?? maskOnly,
        brush: number(value.brush ?? 60, 60, 1, 512),
        softness: number(value.softness ?? 0, 0, 0, 100),
        opacity: number(value.opacity ?? 100, 100, 0, 100),
        grayscale: value.grayscale === true,
        reference: value.reference ? { ...value.reference, ...(value.reference.asset ? { asset: { ...value.reference.asset } } : {}) } : null };
}

// One-pixel antialias ramp for a hard edge; otherwise a flat center followed by
// smoothstep falloff. Distance and diameter are always in source-image pixels.
export function brushCoverage(distance, radius, softness) {
    if (softness === 0) return clamp(radius + .5 - distance, 0, 1);
    const inner = Math.max(0, Math.min(radius - .5, radius * (1 - softness)));
    const t = clamp((distance - inner) / (radius + .5 - inner), 0, 1);
    return 1 - t * t * (3 - 2 * t);
}

function distanceSquared(x, y, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
    const t = length ? clamp(((x - a.x) * dx + (y - a.y) * dy) / length, 0, 1) : 0;
    return (x - a.x - t * dx) ** 2 + (y - a.y - t * dy) ** 2;
}

// A continuous swept circle is the maximum of its radial footprints along a
// segment. Unlike repeated stamps, collinear subdivisions give the same pixels.
// Lazy tile snapshots keep gesture state proportional to the touched area.
export class MaskStroke {
    constructor(width, height, read, { size = 60, softness = 0, opacity = 100, erase = false } = {}) {
        this.width = width; this.height = height; this.read = read;
        this.radius = size / 2; this.softness = softness / 100; this.opacity = opacity / 100;
        this.erase = erase; this.tiles = new Map(); this.dirty = new Set(); this.changed = false;
        this.pixelDelta = 0; this.endpoint = null;
    }
    cover(bounds, coverage, intersects = () => true) {
        if (!this.opacity) return;
        const [left, top, right, bottom] = bounds;
        for (let ty = Math.max(0, Math.floor(top / TILE)); ty < Math.min(Math.ceil(this.height / TILE), Math.ceil(bottom / TILE)); ty++) {
            for (let tx = Math.max(0, Math.floor(left / TILE)); tx < Math.min(Math.ceil(this.width / TILE), Math.ceil(right / TILE)); tx++) {
                const x = tx * TILE, y = ty * TILE;
                const w = Math.min(TILE, this.width - x), h = Math.min(TILE, this.height - y);
                if (!intersects(x + w / 2, y + h / 2, Math.hypot(w, h) / 2)) continue;
                const key = ty * Math.ceil(this.width / TILE) + tx;
                let tile = this.tiles.get(key);
                if (!tile) {
                    const before = new Uint8ClampedArray(this.read(x, y, w, h));
                    tile = { x, y, w, h, before, after: before.slice(), coverage: new Float32Array(w * h) };
                    this.tiles.set(key, tile);
                }
                for (let py = Math.max(0, Math.floor(top - y)); py < Math.min(h, Math.ceil(bottom - y)); py++) {
                    for (let px = Math.max(0, Math.floor(left - x)); px < Math.min(w, Math.ceil(right - x)); px++) {
                        const i = py * w + px;
                        if (tile.coverage[i] === 1) continue;
                        const c = Math.fround(coverage(x + px, y + py));
                        if (c <= tile.coverage[i]) continue;
                        tile.coverage[i] = c;
                        const alpha = i * 4 + 3, initial = tile.before[alpha], amount = c * this.opacity;
                        const value = Math.round(this.erase ? initial * (1 - amount) : initial + (255 - initial) * amount);
                        if (value === tile.after[alpha]) continue;
                        this.pixelDelta += Number(value > 0) - Number(tile.after[alpha] > 0);
                        tile.after[alpha] = value;
                        tile.after[alpha - 3] = tile.after[alpha - 2] = tile.after[alpha - 1] = value ? 255 : 0;
                        this.dirty.add(tile); this.changed = true;
                    }
                }
            }
        }
    }
    segment(a, b = a) {
        if (a.x === b.x && a.y === b.y && this.endpoint?.x === b.x && this.endpoint?.y === b.y) return;
        this.endpoint = b;
        const edge = this.radius + .5;
        this.cover([Math.min(a.x, b.x) - edge, Math.min(a.y, b.y) - edge,
            Math.max(a.x, b.x) + edge, Math.max(a.y, b.y) + edge],
        (x, y) => {
            const distance = distanceSquared(x + .5, y + .5, a, b);
            return distance >= edge * edge ? 0 : brushCoverage(Math.sqrt(distance), this.radius, this.softness);
        }, (x, y, radius) => distanceSquared(x, y, a, b) <= (edge + radius) ** 2);
    }
    polyline(points) {
        const path = [];
        for (const point of points) {
            const b = path.at(-1), a = path.at(-2);
            if (b?.x === point.x && b?.y === point.y) continue;
            // Collapse only straight, forward subdivisions; retain turns and reversals.
            if (a && (b.x - a.x) * (point.y - b.y) === (b.y - a.y) * (point.x - b.x)
                && (b.x - a.x) * (point.x - b.x) + (b.y - a.y) * (point.y - b.y) >= 0) path.pop();
            path.push(point);
        }
        if (path.length === 1) this.segment(path[0]);
        for (let i = 1; i < path.length; i++) this.segment(path[i - 1], path[i]);
    }
    rectangle(a, b) {
        const left = Math.min(a.x, b.x), top = Math.min(a.y, b.y);
        const right = Math.max(a.x, b.x), bottom = Math.max(a.y, b.y);
        this.cover([left, top, right, bottom], (x, y) =>
            Math.max(0, Math.min(x + 1, right) - Math.max(x, left)) *
            Math.max(0, Math.min(y + 1, bottom) - Math.max(y, top)));
    }
    flush(write) {
        const changed = this.dirty.size > 0;
        for (const tile of this.dirty) write(tile.x, tile.y, tile.w, tile.h, tile.after);
        this.dirty.clear();
        return changed;
    }
    cancel(write) {
        if (this.changed) for (const tile of this.tiles.values()) write(tile.x, tile.y, tile.w, tile.h, tile.before);
        this.tiles.clear(); this.dirty.clear();
    }
}
