export const PREVIEW_EDGE = 1536;
export const MAX_POINTS = 100000;

export function validateStrokes(strokes) {
    if (!Array.isArray(strokes) || strokes.length > 2000) throw Error("Liquify supports up to 2000 strokes.");
    let count = 0;
    for (const stroke of strokes) {
        if (!Number.isFinite(stroke?.radius) || stroke.radius < 0.0001 || stroke.radius > 1
            || !Number.isFinite(stroke.strength) || stroke.strength < 0 || stroke.strength > 1
            || !Array.isArray(stroke.points) || !stroke.points.length) throw Error("Invalid Liquify stroke.");
        count += stroke.points.length;
        if (count > MAX_POINTS) throw Error("Liquify history exceeds 100,000 points.");
        for (const point of stroke.points) {
            if (!Array.isArray(point) || point.length !== 2 || point.some(v => !Number.isFinite(v) || v < -1 || v > 2)) {
                throw Error("Invalid Liquify stroke coordinates.");
            }
        }
    }
}

export function strokeDabs(stroke, width, height, from = 1) {
    const radius = stroke.radius * Math.max(width, height), dabs = [];
    for (let i = from; i < stroke.points.length; i++) {
        const a = stroke.points[i - 1], b = stroke.points[i];
        const dx = (b[0] - a[0]) * width, dy = (b[1] - a[1]) * height;
        const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy) / (radius * 0.25) - 1e-9));
        if (!Number.isFinite(steps) || dabs.length + steps > 500000) throw Error("Liquify stroke is too large to preview.");
        for (let k = 1; k <= steps; k++) {
            dabs.push([a[0] * width + dx * k / steps, a[1] * height + dy * k / steps,
                radius, dx / steps * stroke.strength, dy / steps * stroke.strength]);
        }
    }
    return dabs;
}

export class LiquifyWarp {
    constructor(width, height, source) {
        this.width = width; this.height = height;
        this.source = new Uint8ClampedArray(source);
        this.output = new Uint8ClampedArray(source);
        this.dx = new Float32Array(width * height);
        this.dy = new Float32Array(width * height);
    }

    apply(stroke, from = 1) {
        const { width: w, height: h } = this;
        const dirty = [w, h, 0, 0];
        for (const [cx, cy, radius, mx, my] of strokeDabs(stroke, w, h, from)) {
            const x0 = Math.max(0, Math.floor(cx - radius)), x1 = Math.min(w, Math.ceil(cx + radius));
            const y0 = Math.max(0, Math.floor(cy - radius)), y1 = Math.min(h, Math.ceil(cy + radius));
            if (x0 >= x1 || y0 >= y1) continue;
            dirty[0] = Math.min(dirty[0], x0); dirty[1] = Math.min(dirty[1], y0);
            dirty[2] = Math.max(dirty[2], x1); dirty[3] = Math.max(dirty[3], y1);
            for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
                const t = Math.max(0, 1 - Math.hypot(x - cx, y - cy) / radius);
                const weight = t * t * (3 - 2 * t), i = y * w + x;
                this.dx[i] -= mx * weight; this.dy[i] -= my * weight;
            }
        }
        return dirty;
    }

    render([left, top, right, bottom] = [0, 0, this.width, this.height]) {
        const { width: w, height: h, source: src, output: out, dx, dy } = this;
        for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) {
            const i = y * w + x, sx = Math.min(w - 1, Math.max(0, x + dx[i]));
            const sy = Math.min(h - 1, Math.max(0, y + dy[i]));
            const x0 = Math.floor(sx), y0 = Math.floor(sy), x1 = Math.min(w - 1, x0 + 1), y1 = Math.min(h - 1, y0 + 1);
            const fx = sx - x0, fy = sy - y0;
            for (let c = 0; c < 4; c++) out[i * 4 + c] =
                src[(y0 * w + x0) * 4 + c] * (1 - fx) * (1 - fy) + src[(y0 * w + x1) * 4 + c] * fx * (1 - fy)
                + src[(y1 * w + x0) * 4 + c] * (1 - fx) * fy + src[(y1 * w + x1) * 4 + c] * fx * fy;
        }
        return out;
    }

    replay(strokes) {
        this.dx.fill(0); this.dy.fill(0);
        for (const stroke of strokes) this.apply(stroke);
        return this.render();
    }
}
