import assert from "node:assert/strict";
import { test } from "node:test";
import { LiquifyWarp, strokeDabs, validateStrokes } from "../js/liquify_warp.mjs";

const width = 80, height = 60;
const source = Uint8ClampedArray.from({ length: width * height * 4 }, (_, i) => (i * 17) % 256);
const stroke = { radius: 0.2, strength: 0.7, points: [[0.3, 0.4], [0.5, 0.5], [0.6, 0.6]] };

test("invalid saved coordinates and zero radius cannot enter the renderer", () => {
    assert.throws(() => validateStrokes([{ ...stroke, radius: 0 }]));
    assert.throws(() => validateStrokes([{ ...stroke, points: [[NaN, 0]] }]));
    assert.throws(() => validateStrokes([{ ...stroke, points: [[1e20, 0]] }]));
    validateStrokes([stroke]);
});

test("replaying strokes restores exactly the same pixels and leaves the source intact", () => {
    const warp = new LiquifyWarp(width, height, source);
    warp.apply(stroke); warp.render(); const painted = warp.output.slice();
    assert.notDeepEqual(painted, source);
    warp.replay([]); assert.deepEqual(warp.output, source);
    warp.replay([stroke]); assert.deepEqual(warp.output, painted);
    assert.deepEqual(warp.source, source);
});

test("frame grouping does not change the warp", () => {
    const incremental = new LiquifyWarp(width, height, source), complete = new LiquifyWarp(width, height, source);
    incremental.apply({ ...stroke, points: stroke.points.slice(0, 2) });
    incremental.render(); incremental.apply(stroke, 2); incremental.render();
    complete.replay([stroke]); assert.deepEqual(incremental.output, complete.output);
});

test("dabs and brush strength scale with source dimensions", () => {
    const small = strokeDabs(stroke, width, height), big = strokeDabs(stroke, width * 4, height * 4);
    assert.equal(small.length, big.length);
    small.forEach((dab, i) => dab.forEach((v, j) => assert.ok(Math.abs(big[i][j] - 4 * v) < 1e-10)));
});

test("different editor instances own independent history and pixels", () => {
    const first = new LiquifyWarp(width, height, source), second = new LiquifyWarp(width, height, source);
    first.replay([stroke]); assert.deepEqual(second.output, source);
    second.replay([{ ...stroke, strength: 0.1 }]); assert.notDeepEqual(first.output, second.output);
});

test("outside brush samples cannot access pixels beyond the source", () => {
    const warp = new LiquifyWarp(width, height, source);
    warp.replay([{ ...stroke, points: [[-1, -1], [2, 2]] }]);
    assert.equal(warp.output.length, source.length);
    assert.ok(warp.dx.every(Number.isFinite)); assert.ok(warp.dy.every(Number.isFinite));
});
