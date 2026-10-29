import test from "node:test";
import assert from "node:assert/strict";
import { describe, fitWindow, gridFrames, hitTest, makeLayout, parseLength, seekTime, tickStep } from "../js/ripple_timeline.mjs";

test("lengths parse from labels and fall back to 107", () => {
    assert.equal(parseLength("56 frames (2.33 s)"), 56);
    assert.equal(parseLength("50 frames"), 107);
});

test("windows are clamped, never padded", () => {
    assert.deepEqual(fitWindow(300, 250, 107, 10), { start: 193, length: 107, edit: 10 });
    assert.deepEqual(fitWindow(60, 0, 107, 90), { start: 0, length: 56, edit: 55 });
    assert.deepEqual(fitWindow(300, -5, 22, -1), { start: 0, length: 22, edit: 0 });
});

test("grid frames match ripple_core.grid_frame_count", () => {
    assert.equal(gridFrames(5.041667), 121);
    assert.equal(gridFrames(10, 0.5), 228);
});

test("seek lands just before the half-grid point (FFmpeg fps round=near)", () => {
    assert.ok(Math.abs(seekTime(24, { fps: 24, first_time: 0, duration: 10 }) - (24.5 / 24 - 1e-4)) < 1e-9);
    // 60 fps grid frame 200: FFmpeg keeps source frame 501 (8.350 s); seek must land in [8.350, 8.3667).
    const t = seekTime(200, { fps: 60, first_time: 0, duration: 20 });
    assert.ok(t >= 501 / 60 && t < 502 / 60);
    assert.ok(seekTime(10000, { fps: 24, first_time: 0, duration: 10 }) < 10);
});

test("hit test prefers the marker over the window", () => {
    const layout = makeLayout(10, 480, 480);
    assert.equal(hitTest(layout.x(160.5), layout, 100, 107, 60), "marker");
    assert.equal(hitTest(layout.x(120), layout, 100, 107, 60), "window");
    assert.equal(hitTest(layout.x(20), layout, 100, 107, 60), "track");
});

test("readout and ticks", () => {
    assert.equal(describe(240, 107, 60), "00:12.50 · frame 61 of 107 · window 00:10.00–00:14.46 (4.46 s)");
    assert.equal(tickStep(60, 480), 10);
});

import { freeFrame, hitMarker, moveMarker, parseMarkers, suggestFrame } from "../js/ripple_timeline.mjs";

test("markers parse, sort, dedupe, clamp, and fall back to edit_frame", () => {
    assert.deepEqual(parseMarkers("", 7, 22), [{ frame: 7, edited: "", edited_at: null }]);
    const parsed = parseMarkers(JSON.stringify([{ frame: 30, edited: "ripple_edits/a.png", edited_at: 130 }, 3, { frame: 3 }]), 0, 22);
    assert.deepEqual(parsed.map((m) => m.frame), [3, 21]);
    assert.equal(parsed[1].edited, "ripple_edits/a.png");
    assert.equal(parseMarkers("not json", 2, 22)[0].frame, 2);
    assert.equal(parseMarkers(JSON.stringify([0, 1, 2, 3, 4, 5]), 0, 22).length, 4);
});

test("new markers go to the widest gap on a free frame", () => {
    assert.equal(suggestFrame([10], 107), 58);
    assert.equal(suggestFrame([], 22), 10);
    assert.equal(freeFrame([5, 6], 5, 22), 4);
    assert.equal(freeFrame([0, 1], 0, 2), -1);
});

test("marker hit test and collision-free moves", () => {
    const layout = makeLayout(10, 480, 480);
    assert.equal(hitMarker(layout.x(160.5), layout, 100, [10, 60]), 1);
    assert.equal(hitMarker(layout.x(300), layout, 100, [10, 60]), -1);
    assert.equal(moveMarker([10, 20], 0, 20, 107), 19);
    assert.equal(moveMarker([10, 20], 1, 10, 107), 11);
    assert.equal(moveMarker([10, 20], 0, 200, 107), 106);
});

import { aspectMismatch, canvasSize, combinationNote, defaultMarkers, rescaleMarkers, shortEdge } from "../js/ripple_timeline.mjs";

test("canvas size and short edge match ripple_core", () => {
    assert.equal(shortEdge("384 (trained up to 107 frames)"), 384);
    assert.equal(shortEdge("nonsense"), 384);
    assert.deepEqual(canvasSize(1920, 1080, 384), { width: 672, height: 384 });
    assert.deepEqual(canvasSize(1080, 1920, 544), { width: 544, height: 960 });
    assert.deepEqual(canvasSize(1280, 704, 352), { width: 640, height: 352 });
    assert.equal(canvasSize(0, 10, 384), null);
});

test("untrained size/length pairs are flagged", () => {
    assert.equal(combinationNote("384 (trained up to 107 frames)", 107), null);
    assert.equal(combinationNote("352 (trained up to 124 frames)", 124), null);
    assert.match(combinationNote("544 (trained up to 56 frames)", 107), /trained up to 56 frames/);
    assert.match(combinationNote("768 (untested)", 22), /untested/);
});

test("two default markers and length changes", () => {
    assert.deepEqual(defaultMarkers(107).map((m) => m.frame), [26, 80]);
    const markers = [{ frame: 26, edited: "", edited_at: null }, { frame: 80, edited: "e.png", edited_at: 80 }];
    assert.deepEqual(rescaleMarkers(markers, 107, 39).map((m) => m.frame), [9, 38]);
    assert.deepEqual(rescaleMarkers(markers, 107, 124).map((m) => m.frame), [30, 80]);
    assert.deepEqual(rescaleMarkers(markers, 107, 107).map((m) => m.frame), [26, 80]);
});

test("edited images with another aspect are noticed", () => {
    assert.equal(aspectMismatch(1344, 768, { width: 672, height: 384 }), false);
    assert.equal(aspectMismatch(1024, 1024, { width: 672, height: 384 }), true);
    assert.equal(aspectMismatch(0, 0, { width: 672, height: 384 }), false);
});
