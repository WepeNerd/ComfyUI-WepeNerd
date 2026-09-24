// Pure curve math for the Sigma Curve node. Mirrored exactly by sigma_curve_node.py.
// Points are [x, y]: x in 0..1 is the position along the schedule, y in 0..1 is
// sigma / sigma_max. The first and last points are pinned to x = 0 and x = 1.

export const MIN_GAP = 1e-4;
export const MAX_POINTS = 64;

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const finite = (v, fallback) => (Number.isFinite(v) ? v : fallback);

export function normalizePoints(points) {
    let list = Array.isArray(points) ? points : [];
    list = list
        .filter((p) => Array.isArray(p) && p.length >= 2)
        .map((p) => [clamp(finite(Number(p[0]), 0), 0, 1), clamp(finite(Number(p[1]), 0), 0, 1)])
        .sort((a, b) => a[0] - b[0]);
    const out = [];
    for (const p of list) {
        if (out.length && p[0] - out[out.length - 1][0] < MIN_GAP) continue;
        out.push(p);
    }
    if (!out.length) return [[0, 1], [1, 0]];
    if (out.length === 1) return [[0, out[0][1]], [1, out[0][1]]];
    out[0][0] = 0;
    out[out.length - 1][0] = 1;
    return out.slice(0, MAX_POINTS - 1).concat(out.length > MAX_POINTS ? [out[out.length - 1]] : []);
}

// Fritsch-Carlson monotone cubic tangents: no overshoot between control points.
export function monotoneTangents(points) {
    const n = points.length;
    const d = [];
    for (let i = 0; i < n - 1; i++) {
        d.push((points[i + 1][1] - points[i][1]) / (points[i + 1][0] - points[i][0]));
    }
    const m = new Array(n).fill(0);
    m[0] = d[0];
    m[n - 1] = d[n - 2];
    for (let i = 1; i < n - 1; i++) {
        m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
    }
    for (let i = 0; i < n - 1; i++) {
        if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
        const a = m[i] / d[i], b = m[i + 1] / d[i];
        const s = a * a + b * b;
        if (s > 9) {
            const t = 3 / Math.sqrt(s);
            m[i] = t * a * d[i];
            m[i + 1] = t * b * d[i];
        }
    }
    return m;
}

export const MODES = ["log smooth", "smooth", "linear"];
export const LOG_FLOOR = 1e-5;

const toLog = (y) => Math.log(Math.max(y, LOG_FLOOR));

// Build an evaluator once per curve. "log smooth" runs the monotone cubic on
// log(sigma), so exponential-style schedules are straight lines between points.
export function makeEvaluator(points, mode = "log smooth") {
    const pts = normalizePoints(points);
    if (mode === "linear") return (x) => evaluate(pts, x, "linear");
    if (mode === "smooth") {
        const m = monotoneTangents(pts);
        return (x) => evaluate(pts, x, "smooth", m);
    }
    const logPts = pts.map(([x, y]) => [x, toLog(y)]);
    const m = monotoneTangents(logPts);
    return (x) => {
        const y = Math.exp(hermite(logPts, x, m));
        return y <= LOG_FLOOR * 1.000001 ? 0 : clamp(y, 0, 1);
    };
}

function segment(points, x) {
    let i = 0;
    while (i < points.length - 2 && x > points[i + 1][0]) i++;
    return i;
}

function hermite(points, x, m) {
    const n = points.length;
    if (x <= points[0][0]) return points[0][1];
    if (x >= points[n - 1][0]) return points[n - 1][1];
    const i = segment(points, x);
    const [x0, y0] = points[i], [x1, y1] = points[i + 1];
    const h = x1 - x0, t = (x - x0) / h, t2 = t * t, t3 = t2 * t;
    return (2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * h * m[i]
        + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * h * m[i + 1];
}

export function evaluate(points, x, mode = "smooth", tangents = null) {
    const n = points.length;
    if (x <= points[0][0]) return points[0][1];
    if (x >= points[n - 1][0]) return points[n - 1][1];
    if (mode === "linear") {
        const i = segment(points, x);
        const [x0, y0] = points[i], [x1, y1] = points[i + 1];
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
    }
    return clamp(hermite(points, x, tangents || monotoneTangents(points)), 0, 1);
}

// Positions (0..1) where the schedule is sampled. With endAtZero the curve
// supplies `steps` sigmas and a final 0 is appended (ComfyUI convention).
export function samplePositions(steps, endAtZero = true) {
    steps = Math.max(1, Math.round(Number(steps) || 1));
    const count = endAtZero ? steps : steps + 1;
    const out = [];
    for (let i = 0; i < count; i++) out.push(count === 1 ? 0 : i / (count - 1));
    return out;
}

export function sampleSigmas(points, steps, sigmaMax, mode = "log smooth", endAtZero = true) {
    const f = makeEvaluator(points, mode);
    const out = samplePositions(steps, endAtZero).map((x) => f(x) * sigmaMax);
    if (endAtZero) out.push(0);
    return out;
}

// ---- presets ---------------------------------------------------------------
// Each returns sigma at t in 0..1 (0 = first step, 1 = last curve step).

const AYS_SDXL = [14.615, 6.315, 3.771, 2.181, 1.342, 0.862, 0.555, 0.380, 0.234, 0.113, 0.029];

function logLinear(values, t) {
    const pos = t * (values.length - 1);
    const i = Math.min(values.length - 2, Math.floor(pos));
    const f = pos - i;
    return Math.exp(Math.log(values[i]) * (1 - f) + Math.log(values[i + 1]) * f);
}

export const PRESETS = {
    karras: { mode: "log smooth", label: "Karras", fn: (t, lo, hi) => {
        const rho = 7, a = hi ** (1 / rho), b = lo ** (1 / rho);
        return (a + t * (b - a)) ** rho;
    } },
    exponential: { mode: "log smooth", label: "Exponential", fn: (t, lo, hi) => Math.exp(Math.log(hi) + t * (Math.log(lo) - Math.log(hi))) },
    polyexp: { mode: "log smooth", label: "Poly-exponential (ρ 2)", fn: (t, lo, hi) => {
        const r = t ** 2; return Math.exp(Math.log(hi) + r * (Math.log(lo) - Math.log(hi)));
    } },
    linear: { mode: "linear", label: "Linear", fn: (t, lo, hi) => hi + t * (lo - hi) },
    ays_sdxl: { mode: "log smooth", label: "Align Your Steps (SDXL)", fn: (t, lo, hi) => {
        const s = logLinear(AYS_SDXL, t) / AYS_SDXL[0];
        return lo + (s - AYS_SDXL[10] / AYS_SDXL[0]) / (1 - AYS_SDXL[10] / AYS_SDXL[0]) * (hi - lo);
    } },
    flow3: { mode: "smooth", label: "Flow · shift 3", fn: (t, lo, hi) => {
        const u = 1 - t, s = 3 * u / (1 + 2 * u); return lo + s * (hi - lo);
    } },
    flow6: { mode: "smooth", label: "Flow · shift 6", fn: (t, lo, hi) => {
        const u = 1 - t, s = 6 * u / (1 + 5 * u); return lo + s * (hi - lo);
    } },
    cosine: { mode: "smooth", label: "Cosine", fn: (t, lo, hi) => lo + (hi - lo) * (Math.cos(t * Math.PI) + 1) / 2 },
};

// Greedy fit: start from the endpoints and add the control point where the
// smooth curve misses the preset most (error in log-sigma space) until close.
export function fitCurve(fn, sigmaMin, sigmaMax, { mode = "log smooth", maxPoints = 8, tolerance = 0.02 } = {}) {
    const lo = Math.max(1e-6, Math.min(sigmaMin, sigmaMax)), hi = Math.max(sigmaMin, sigmaMax, 1e-6);
    const target = (t) => clamp(fn(t, lo, hi) / hi, 0, 1);
    const grid = Array.from({ length: 81 }, (_, i) => i / 80);
    const floor = lo / hi / 2;
    const err = (a, b) => Math.abs(Math.log(Math.max(a, floor)) - Math.log(Math.max(b, floor)));
    let pts = [[0, target(0)], [1, target(1)]];
    while (pts.length < maxPoints) {
        const f = makeEvaluator(pts, mode);
        let worst = -1, worstX = 0;
        for (const x of grid) {
            if (pts.some((p) => Math.abs(p[0] - x) < 0.035)) continue;
            const e = err(f(x), target(x));
            if (e > worst) { worst = e; worstX = x; }
        }
        if (worst < tolerance) break;
        pts = normalizePoints([...pts, [worstX, target(worstX)]]);
    }
    return pts.map(([x, y]) => [round(x), round(y)]);
}

export function round(v, digits = 6) {
    const k = 10 ** digits; return Math.round(v * k) / k;
}

export function parseCurve(text) {
    let data = {};
    try { data = typeof text === "string" && text.trim() ? JSON.parse(text) : (text || {}); } catch { data = {}; }
    if (Array.isArray(data)) data = { points: data };
    return {
        points: normalizePoints(data.points),
        log: !!data.log,
        snap: data.snap !== false,
    };
}

export function serializeCurve(state) {
    return JSON.stringify({ v: 1, points: state.points.map(([x, y]) => [round(x), round(y)]), log: !!state.log, snap: !!state.snap });
}

export const DEFAULT_SIGMA_MAX = 14.614642;
export const DEFAULT_SIGMA_MIN = 0.0291675;
export const DEFAULT_CURVE = serializeCurve({
    points: fitCurve(PRESETS.karras.fn, DEFAULT_SIGMA_MIN, DEFAULT_SIGMA_MAX), log: false, snap: true,
});
