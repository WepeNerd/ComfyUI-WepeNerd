import assert from "node:assert/strict";
import { test } from "node:test";
import {
    DEFAULT_CURVE, PRESETS, fitCurve, makeEvaluator, normalizePoints, parseCurve,
    samplePositions, sampleSigmas,
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

test("presets fit their reference schedules closely", () => {
    for (const [key, preset] of Object.entries(PRESETS)) {
        const pts = fitCurve(preset.fn, 0.0291675, 14.614642, { mode: preset.mode });
        assert.ok(pts.length <= 8, key);
        const got = sampleSigmas(pts, 20, 14.614642, preset.mode, true);
        for (let i = 0; i < 20; i++) {
            const want = preset.fn(i / 19, 0.0291675, 14.614642);
            assert.ok(Math.abs(Math.log(got[i] / want)) < 0.15, `${key} step ${i}: ${got[i]} vs ${want}`);
        }
    }
});

test("parses saved curves defensively", () => {
    assert.deepEqual(parseCurve("not json").points, [[0, 1], [1, 0]]);
    assert.deepEqual(parseCurve("[[0,0.5],[1,0.1]]").points, [[0, 0.5], [1, 0.1]]);
    const saved = parseCurve(DEFAULT_CURVE);
    assert.equal(saved.snap, true);
    assert.ok(saved.points.length >= 3);
});
