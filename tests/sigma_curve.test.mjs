import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
    DEFAULT_CURVE, MAX_POINTS, PRESETS, fitCurve, makeEvaluator, normalizePoints, parseCurve,
    parseSigmaList, samplePositions, sampleSigmas,
} from "../js/sigma_curve.mjs";

// Shared with tests/test_sigma_curve.py: the Python node must output the same values.
const PARITY_POINTS = [[0, 1], [0.3, 0.2], [0.55, 0.25], [0.8, 0.02], [1, 0]];
const PARITY = {
    "log smooth": [14.614642, 4.792817, 2.954795, 3.569847, 2.357055, 0.113735, 0, 0],
    smooth: [14.614642, 6.515444, 2.958437, 3.577664, 2.265727, 0.174953, 0, 0],
    linear: [14.614642, 8.119246, 3.020359, 3.507514, 2.085022, 0.243577, 0, 0],
};

test("normalizes unsorted, duplicate, and out-of-range points with pinned ends", () => {
    assert.deepEqual(normalizePoints([[0.5, 2], [0.2, 0.5], [0.2, 0.9], [0.9, -1]]),
        [[0, 0.5], [0.5, 1], [1, 0]]);
    assert.deepEqual(normalizePoints([]), [[0, 1], [1, 0]]);
    assert.deepEqual(normalizePoints([[0.4, 0.3]]), [[0, 0.3], [1, 0.3]]);
});

test("sample positions follow the ComfyUI steps + trailing zero convention", () => {
    assert.deepEqual(samplePositions(3, true), [0, 0.5, 1]);
    assert.deepEqual(samplePositions(2, false), [0, 0.5, 1]);
    assert.deepEqual(samplePositions(1, true), [0]);
    const s = sampleSigmas([[0, 1], [1, 0.1]], 4, 10, "linear", true);
    assert.equal(s.length, 5);
    assert.equal(s.at(-1), 0);
    assert.equal(s[0], 10);
});

test("curves pass through their control points and never overshoot", () => {
    for (const mode of ["log smooth", "smooth", "linear"]) {
        const f = makeEvaluator(PARITY_POINTS, mode);
        for (const [x, y] of PARITY_POINTS) assert.ok(Math.abs(f(x) - y) < 1e-9, `${mode} @${x}`);
        for (let i = 0; i < PARITY_POINTS.length - 1; i++) {
            const [x0, y0] = PARITY_POINTS[i], [x1, y1] = PARITY_POINTS[i + 1];
            for (let k = 1; k < 20; k++) {
                const y = f(x0 + (x1 - x0) * k / 20);
                assert.ok(y <= Math.max(y0, y1) + 1e-9 && y >= Math.min(y0, y1) - 1e-9, `${mode} segment ${i}`);
            }
        }
    }
});

test("matches the Python parity fixture", () => {
    for (const [mode, expected] of Object.entries(PARITY)) {
        const got = sampleSigmas(PARITY_POINTS, 7, 14.614642, mode, true);
        got.forEach((v, i) => assert.ok(Math.abs(v - expected[i]) < 1e-5, `${mode}[${i}] ${v}`));
    }
});

// Independent re-implementations of ComfyUI's schedulers (k_diffusion/sampling.py,
// samplers.py simple_scheduler + model_sampling.py time_snr_shift, and
// comfy_extras/nodes_align_your_steps.py). Each returns the full SIGMAS list.
const LO = 0.0291675, HI = 14.614642;
const linspace = (a, b, n) => Array.from({ length: n }, (_, i) => (n === 1 ? a : a + (b - a) * i / (n - 1)));
const withZero = (a) => [...a, 0];
const AYS = [14.6146412293, 6.3184485287, 3.7681790315, 2.1811480769, 1.3405244945,
    0.8620721141, 0.5550693289, 0.3798540708, 0.2332364134, 0.1114188177, 0.0291671582];
function loglinearInterp(values, num) {
    const xs = linspace(0, 1, values.length), ys = values.slice().reverse().map(Math.log);
    return linspace(0, 1, num).map((x) => {
        const i = Math.min(xs.length - 2, Math.floor(x * (xs.length - 1)));
        const f = (x - xs[i]) / (xs[i + 1] - xs[i]);
        return Math.exp(ys[i] + f * (ys[i + 1] - ys[i]));
    }).reverse();
}
function comfyFlowSimple(shift, n) {
    const table = Array.from({ length: 1000 }, (_, k) => { const t = (k + 1) / 1000; return shift * t / (1 + (shift - 1) * t); });
    const ss = 1000 / n;
    return withZero(Array.from({ length: n }, (_, x) => table[table.length - 1 - Math.floor(x * ss)]));
}
const COMFY = {
    karras: (n) => { const a = HI ** (1 / 7), b = LO ** (1 / 7); return withZero(linspace(0, 1, n).map((t) => (a + t * (b - a)) ** 7)); },
    exponential: (n) => withZero(linspace(Math.log(HI), Math.log(LO), n).map(Math.exp)),
    polyexp: (n) => withZero(linspace(1, 0, n).map((r) => Math.exp(r ** 2 * (Math.log(HI) - Math.log(LO)) + Math.log(LO)))),
    linear: (n) => withZero(linspace(HI, LO, n)),
    ays_sdxl: (n) => { const s = n + 1 === AYS.length ? AYS.slice() : loglinearInterp(AYS, n + 1); s[s.length - 1] = 0; return s; },
    flow3: (n) => comfyFlowSimple(3, n),
    flow6: (n) => comfyFlowSimple(6, n),
};
const PRESET_TOLERANCE = { ays_sdxl: 0.025 };

test("presets match ComfyUI's schedulers step for step", () => {
    for (const [key, ref] of Object.entries(COMFY)) {
        const preset = PRESETS[key], flow = key.startsWith("flow");
        const hi = flow ? 1 : HI;
        for (const steps of [1, 4, 8, 10, 20, 30, 50]) {
            const pts = fitCurve(preset.fn, LO, hi, { mode: preset.mode, steps, endZero: true });
            assert.ok(pts.length <= 12, `${key}: ${pts.length} points`);
            const got = sampleSigmas(pts, steps, hi, preset.mode, true), want = ref(steps);
            assert.equal(got.length, want.length, `${key} length @${steps}`);
            const tol = PRESET_TOLERANCE[key] ?? 0.006;
            want.forEach((w, i) => {
                if (w === 0) assert.equal(got[i], 0, `${key} @${steps}[${i}]`);
                else assert.ok(Math.abs(got[i] / w - 1) < tol, `${key} @${steps}[${i}]: ${got[i]} vs ${w}`);
            });
        }
    }
});

test("flow presets without the trailing zero end at 0 like ComfyUI", () => {
    for (const steps of [6, 20]) {
        const pts = fitCurve(PRESETS.flow3.fn, LO, 1, { mode: "smooth", steps, endZero: false });
        const got = sampleSigmas(pts, steps, 1, "smooth", false), want = COMFY.flow3(steps);
        assert.equal(got.length, want.length);
        assert.equal(got.at(-1), 0);
        want.slice(0, -1).forEach((w, i) => assert.ok(Math.abs(got[i] / w - 1) < 0.006, `${i}: ${got[i]} vs ${w}`));
    }
});

test("cosine preset follows its formula to the last steps", () => {
    const pts = fitCurve(PRESETS.cosine.fn, LO, HI, { mode: "smooth" });
    for (const steps of [10, 30, 50]) {
        const got = sampleSigmas(pts, steps, HI, "smooth", true);
        for (let i = 0; i < steps; i++) {
            const want = PRESETS.cosine.fn(i / (steps - 1), LO, HI);
            assert.ok(Math.abs(got[i] / want - 1) < 0.03, `@${steps}[${i}]: ${got[i]} vs ${want}`);
        }
    }
});

test("exactly MAX_POINTS points keep the pinned end point", () => {
    const pts = Array.from({ length: MAX_POINTS }, (_, i) => [i / (MAX_POINTS - 1), 1 - 0.99 * i / (MAX_POINTS - 1)]);
    const out = normalizePoints(pts);
    assert.equal(out.length, MAX_POINTS);
    assert.equal(out.at(-1)[0], 1);
    assert.ok(Math.abs(out.at(-1)[1] - 0.01) < 1e-12);
    const over = normalizePoints([...pts, [0.5, 0.5]]);
    assert.equal(over.length, MAX_POINTS);
    assert.equal(over.at(-1)[0], 1);
});

test("pasted sigma lists keep scientific notation", () => {
    assert.deepEqual(parseSigmaList("14.6, 0.5, 1e-05, 0"), [14.6, 0.5, 1e-5, 0]);
    assert.deepEqual(parseSigmaList("tensor([1.4615e+01, 6.3184e+00, 2.9167e-02, 0.0000e+00])"),
        [14.615, 6.3184, 0.029167, 0]);
    assert.deepEqual(parseSigmaList("tensor([1.0000, 0.5000, 0.0000], device='cuda:0', dtype=torch.float32)"),
        [1, 0.5, 0]);
    assert.deepEqual(parseSigmaList("14.6\n3.2\n0"), [14.6, 3.2, 0]);
    assert.deepEqual(parseSigmaList("nothing here"), []);
});

test("default curve matches the Python node's default", () => {
    const py = readFileSync(new URL("../sigma_curve_node.py", import.meta.url), "utf8");
    const block = py.match(/DEFAULT_CURVE = \(([\s\S]*?)\n\)/)[1];
    const joined = [...block.matchAll(/'([^']*)'/g)].map((m) => m[1]).join("");
    assert.equal(joined, DEFAULT_CURVE);
});

test("parses saved curves defensively", () => {
    assert.deepEqual(parseCurve("not json").points, [[0, 1], [1, 0]]);
    assert.deepEqual(parseCurve("[[0,0.5],[1,0.1]]").points, [[0, 0.5], [1, 0.1]]);
    const saved = parseCurve(DEFAULT_CURVE);
    assert.equal(saved.snap, true);
    assert.ok(saved.points.length >= 3);
});
