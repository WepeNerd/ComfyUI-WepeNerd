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
    return out.length > MAX_POINTS ? out.slice(0, MAX_POINTS - 1).concat([out[out.length - 1]]) : out;
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
// Each fn returns sigma at t in 0..1 (0 = first step, 1 = last curve step) for the
// range lo..hi. `opts` holds { steps, endZero } for schedules that, like ComfyUI's,
// depend on the step count: their last real step is not sigma_min, so the curve is
// fitted so its end lands on ComfyUI's last nonzero sigma for the current steps.

// ComfyUI's Align Your Steps noise levels (comfy_extras/nodes_align_your_steps.py).
export const AYS_SDXL = [14.6146412293, 6.3184485287, 3.7681790315, 2.1811480769, 1.3405244945,
    0.8620721141, 0.5550693289, 0.3798540708, 0.2332364134, 0.1114188177, 0.0291671582];

// Log-linear interpolation through `values` at u in 0..1, like ComfyUI's loglinear_interp.
function logLinear(values, u) {
    const pos = clamp(u, 0, 1) * (values.length - 1);
    const i = Math.min(values.length - 2, Math.floor(pos));
    const f = pos - i;
    return Math.exp(Math.log(values[i]) * (1 - f) + Math.log(values[i + 1]) * f);
}

// Fraction of ComfyUI's schedule covered by the curve's 0..1: with a trailing zero the
// curve's last point is ComfyUI's step steps-1 of steps (the zero is step `steps`).
const tailScale = ({ steps = 20, endZero = true } = {}) => {
    const n = Math.max(1, Math.round(Number(steps) || 1));
    return endZero ? (n - 1) / n : 1;
};

// ComfyUI flow models: time_snr_shift(shift, t) over the model's 1000 timesteps, read
// by the "simple" scheduler at table index int(i * 1000 / steps). Steps land exactly on
// those timesteps; between steps the timestep is interpolated so the curve stays smooth.
const flowShift = (shift) => (t, lo, hi, { steps = 20, endZero = true } = {}) => {
    const n = Math.max(1, Math.round(Number(steps) || 1));
    const timestep = (i) => 1 - Math.floor(i * (1000 / n)) / 1000;
    const pos = t * (endZero ? n - 1 : n), i = Math.floor(pos), f = pos - i;
    const u = f > 0 ? timestep(i) * (1 - f) + timestep(i + 1) * f : timestep(i);
    return hi * shift * u / (1 + (shift - 1) * u);
};

export const PRESETS = {
    karras: { mode: "log smooth", label: "Karras", fn: (t, lo, hi) => {
        const rho = 7, a = hi ** (1 / rho), b = lo ** (1 / rho);
        return (a + t * (b - a)) ** rho;
    } },
    exponential: { mode: "log smooth", label: "Exponential", fn: (t, lo, hi) => Math.exp(Math.log(hi) + t * (Math.log(lo) - Math.log(hi))) },
    // k-diffusion get_sigmas_polyexponential: ramp = (1 - t) ** rho over log sigma.
    polyexp: { mode: "log smooth", label: "Poly-exponential (ρ 2)", fn: (t, lo, hi) => {
        return Math.exp((1 - t) ** 2 * (Math.log(hi) - Math.log(lo)) + Math.log(lo));
    } },
    linear: { mode: "linear", label: "Linear", fn: (t, lo, hi) => hi + t * (lo - hi) },
    // ComfyUI's AYS levels, mapped in log space onto lo..hi (identity at the defaults).
    ays_sdxl: { mode: "log smooth", label: "Align Your Steps (SDXL)", stepDependent: true, fn: (t, lo, hi, opts) => {
        const top = Math.log(AYS_SDXL[0]), bottom = Math.log(AYS_SDXL[AYS_SDXL.length - 1]);
        const f = (Math.log(logLinear(AYS_SDXL, t * tailScale(opts))) - bottom) / (top - bottom);
        return Math.exp(Math.log(lo) + f * (Math.log(hi) - Math.log(lo)));
    } },
    flow3: { mode: "smooth", label: "Flow · shift 3", stepDependent: true, fn: flowShift(3) },
    flow6: { mode: "smooth", label: "Flow · shift 6", stepDependent: true, fn: flowShift(6) },
    cosine: { mode: "smooth", label: "Cosine", fn: (t, lo, hi) => lo + (hi - lo) * (Math.cos(t * Math.PI) + 1) / 2 },
};

export const FIT_POINTS = 12;
export const FIT_TOLERANCE = 0.004;
const FIT_GRID = 200;
const FIT_SPACING = 0.015;

// Greedy fit: start from the endpoints and add the control point where the curve
// misses the target most (error in log-sigma space) until it is within `tolerance`.
export function fitCurve(fn, sigmaMin, sigmaMax, {
    mode = "log smooth", maxPoints = FIT_POINTS, tolerance = FIT_TOLERANCE, steps = 20, endZero = true,
} = {}) {
    const lo = Math.max(1e-6, Math.min(sigmaMin, sigmaMax)), hi = Math.max(sigmaMin, sigmaMax, 1e-6);
    const opts = { steps, endZero };
    const target = (t) => clamp(fn(t, lo, hi, opts) / hi, 0, 1);
    const grid = Array.from({ length: FIT_GRID + 1 }, (_, i) => i / FIT_GRID);
    const floor = lo / hi / 2;
    const err = (a, b) => Math.abs(Math.log(Math.max(a, floor)) - Math.log(Math.max(b, floor)));
    let pts = [[0, target(0)], [1, target(1)]];
    while (pts.length < maxPoints) {
        const f = makeEvaluator(pts, mode);
        let worst = -1, worstX = 0;
        for (const x of grid) {
            if (pts.some((p) => Math.abs(p[0] - x) < FIT_SPACING)) continue;
            const e = err(f(x), target(x));
            if (e > worst) { worst = e; worstX = x; }
        }
        if (worst < tolerance) break;
        pts = normalizePoints([...pts, [worstX, target(worstX)]]);
    }
    return pts.map(([x, y]) => [round(x), round(y)]);
}

// Numbers from pasted text: "14.6, 6.3 … 0", one per line, or a printed
// tensor([1.4615e+01, …]). Words such as tensor/device/dtype are skipped.
export function parseSigmaList(text) {
    return String(text || "")
        .replace(/[\[\](){}]/g, " ")
        .split(/[\s,;]+/)
        .filter((tok) => /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(tok))
        .map(Number)
        .filter(Number.isFinite);
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
