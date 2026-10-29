import test from "node:test";
import assert from "node:assert/strict";
import {
    DURATION_OPTIONS, FPS, MAX_DURATION_FRAMES, aspectMismatch, cropForAspect, cropMatches, defaultMarkers, describe, dragCrop, durationFrames,
    fitWindow, freeFrame, gridFrames, hitMarker, hitTest, makeLayout, moveMarker, outputGeometry,
    parseCrop, parseMarkers, resizeWindow, retainSourceFrames, seekTime, suggestFrame, tickStep,
} from "../js/ripple_timeline.mjs";

test("duration menu contains every native valid count with exact frame and seconds labels", () => {
    assert.equal(DURATION_OPTIONS.length, 212);
    assert.deepEqual(DURATION_OPTIONS[0], [5, "5 frames · 0.21 s"]);
    assert.deepEqual(DURATION_OPTIONS.at(-1), [3592, "3592 frames · 149.67 s"]);
    assert.deepEqual(DURATION_OPTIONS.find(([count]) => count === 107), [107, "107 frames · 4.46 s"]);
    assert.equal(MAX_DURATION_FRAMES, 3592);
    for (const [index, [frames]] of DURATION_OPTIONS.entries()) assert.equal(frames, 5 + index * 17);
});

test("long-source timeline resizing always stays within the duration menu", () => {
    for (const side of ["left", "right"]) {
        const resized = resizeWindow(12000, 6000, 107, side, side === "left" ? 0 : 12000);
        assert.equal(resized.length, MAX_DURATION_FRAMES);
        assert.ok(DURATION_OPTIONS.some(([frames]) => frames === resized.length));
    }
});

test("duration rounds to the nearest H3 count without an eight-second ceiling", () => {
    assert.equal(durationFrames(107 / FPS), 107);
    assert.equal(durationFrames(2), 56);
    assert.equal(durationFrames(12), 294);
    assert.equal(durationFrames(60), 1433);
    assert.equal(durationFrames(0.01), 5);
    assert.equal(durationFrames(13.5 / FPS), 22);
    for (let seconds = 0.1; seconds < 30; seconds += 0.13) {
        const frames = durationFrames(seconds);
        assert.equal((frames - 5) % 17, 0);
        assert.ok(Math.abs(frames - seconds * FPS) <= 8.5);
    }
});

test("windows fit the source without padding or invalid frame counts", () => {
    assert.deepEqual(fitWindow(300, 250, 107), { start: 193, length: 107 });
    assert.deepEqual(fitWindow(60, 0, 107), { start: 0, length: 56 });
    assert.deepEqual(fitWindow(300, -5, 22), { start: 0, length: 22 });
    assert.deepEqual(resizeWindow(300, 100, 107, "right", 140), { start: 100, length: 39 });
    assert.deepEqual(resizeWindow(300, 100, 107, "left", 169), { start: 168, length: 39 });
    for (const edge of ["left", "right"]) for (const frame of [-100, 0, 100, 180, 300, 1000]) {
        const resized = resizeWindow(300, 100, 107, edge, frame);
        assert.ok(resized.start >= 0);
        assert.ok(resized.start + resized.length <= 300);
        assert.equal((resized.length - 5) % 17, 0);
        assert.equal(edge === "left" ? resized.start + resized.length : resized.start, edge === "left" ? 207 : 100);
    }
});

test("resizing keeps surviving anchors on their exact source frames", () => {
    const anchors = [{ id: 1, frame: 26, edited: "a.png", edited_at: 126 }, { id: 2, frame: 80, edited: "b.png", edited_at: 180 }];
    const extended = retainSourceFrames(anchors, 100, 66, 141);
    assert.deepEqual(extended.map((anchor) => 66 + anchor.frame), [126, 180]);
    assert.deepEqual(retainSourceFrames(anchors, 100, 100, 124), anchors);
    assert.deepEqual(anchors.map((anchor) => anchor.frame), [26, 80], "input is not mutated");
});

test("shrinking cannot silently delete the second anchor or its uploaded edit", () => {
    const anchors = [{ id: 1, frame: 60, edited: "a.png", edited_at: 60 }, { id: 2, frame: 90, edited: "b.png", edited_at: 90 }];
    const resized = parseMarkers(JSON.stringify(retainSourceFrames(anchors, 0, 0, 39)), 39);
    assert.equal(resized.length, 2);
    assert.equal(new Set(resized.map((anchor) => anchor.frame)).size, 2);
    for (const anchor of anchors) {
        const retained = resized.find((item) => item.id === anchor.id);
        assert.equal(retained.edited, anchor.edited);
        assert.equal(retained.edited_at, anchor.edited_at, "source binding must remain for the stale warning");
    }
});

test("out-of-window anchors do not displace anchors still inside the selection", () => {
    const result = retainSourceFrames([{ id: 1, frame: 0 }, { id: 2, frame: 10 }, { id: 3, frame: 20 }], 0, 10, 22);
    assert.equal(result.find((m) => m.id === 2).frame, 0);
    assert.equal(result.find((m) => m.id === 3).frame, 10);
    assert.equal(result.find((m) => m.id === 1).frame, 1);
});

test("marker identities survive crossings while batch order stays chronological", () => {
    const crop = [0.2, 0.1, 0.5, 0.6], source = { video: "a.mp4", version: "123" };
    const anchors = [{ id: 1, frame: 90, edited: "a.png", edited_at: 90, edited_crop: crop, edited_source: source },
        { id: 2, frame: 10, edited: "b.png", edited_at: 10 }];
    const sorted = parseMarkers(JSON.stringify(anchors), 107);
    assert.deepEqual(sorted.map((m) => m.id), [2, 1]);
    assert.equal(sorted.find((m) => m.id === 1).edited, "a.png");
    assert.deepEqual(sorted.find((m) => m.id === 1).edited_crop, crop);
    assert.deepEqual(sorted.find((m) => m.id === 1).edited_source, source);
});

test("parser retains every edit on collisions, including excess anchors", () => {
    const anchors = Array.from({ length: 7 }, (_, i) => ({ id: i + 1, frame: 9, edited: i + ".png" }));
    const result = parseMarkers(JSON.stringify(anchors), 5);
    assert.equal(result.length, 7);
    assert.equal(new Set(result.map((m) => m.id)).size, 7);
    assert.equal(new Set(result.map((m) => m.frame)).size, 5);
    assert.equal(result.filter((m) => m.edited).length, 7);
    assert.deepEqual(parseMarkers("", 22), defaultMarkers());
});

test("4K sources use the pixel budget with padding hidden from export", () => {
    assert.deepEqual(outputGeometry(3840, 2160), { width: 666, height: 374, canvasWidth: 672, canvasHeight: 384, crop: [0, 0, 3840, 2160] });
    const preview = outputGeometry(1920, 1080, { megapixels: 1 });
    assert.equal(preview.width, 1332);
    assert.equal(preview.height, 750);
    assert.ok(preview.width * preview.height <= 1e6);
});

test("small sources do not upscale unless requested", () => {
    assert.deepEqual(outputGeometry(320, 180), { width: 320, height: 180, canvasWidth: 320, canvasHeight: 192, crop: [0, 0, 320, 180] });
    assert.equal(outputGeometry(320, 180, { allow_upscale: true }).width, 666);
    const odd = outputGeometry(321, 181);
    assert.equal(odd.width, 320);
    assert.equal(odd.height, 180);
});

test("fit dimensions are bounds and preserve source aspect", () => {
    const result = outputGeometry(1920, 1080, { output_width: 800, output_height: 800 });
    assert.equal(result.width, 800);
    assert.equal(result.height, 450);
    assert.equal(result.canvasWidth, 800);
    assert.equal(result.canvasHeight, 480);
    const portrait = outputGeometry(1080, 1920, { output_width: 800, output_height: 800 });
    assert.equal(portrait.width, 450);
    assert.equal(portrait.height, 800);
});

test("crop is applied in oriented source space before sizing", () => {
    const crop = cropForAspect(16 / 9, 1);
    assert.deepEqual(crop, [0.21875, 0, 0.5625, 1]);
    assert.deepEqual(outputGeometry(1920, 1080, { crop }), { width: 500, height: 500, canvasWidth: 512, canvasHeight: 512, crop: [420, 0, 1080, 1080] });
    assert.deepEqual(parseCrop(""), [0, 0, 1, 1]);
    assert.deepEqual(parseCrop([0.8, -0.2, 0.5, 0.8]), [0.8, 0, 0.2, 0.8]);
    assert.ok(cropMatches("", [0, 0, 1, 1]));
    assert.ok(!cropMatches(crop, [0, 0, 1, 1]));
});

test("MP-only resizing preserves crop geometry and uploaded originals", () => {
    const crop = [0.2, 0.1, 0.5, 0.6];
    const small = outputGeometry(3840, 2160, { crop, megapixels: 0.25 });
    const large = outputGeometry(3840, 2160, { crop, megapixels: 1 });
    assert.deepEqual(small.crop, large.crop);
    const marker = { id: 1, frame: 0, edited: "original-upload.png", edited_at: 0, edited_crop: crop };
    const parsed = parseMarkers(JSON.stringify([marker]), 107)[0];
    assert.ok(cropMatches(parsed.edited_crop, crop));
    assert.equal(parsed.edited, marker.edited);
});

test("crop dragging stays inside the source with locked aspect and opposite corner", () => {
    const crop = [0.2, 0.1, 0.4, 0.6];
    assert.deepEqual(dragCrop(crop, "move", 5, -2), [0.6, 0, 0.4, 0.6]);
    for (const handle of ["nw", "ne", "sw", "se"]) for (const delta of [-5, -0.1, 0.1, 5]) {
        const [x, y, w, h] = dragCrop(crop, handle, delta, delta, 2 / 3);
        assert.ok(x >= -1e-10 && y >= -1e-10 && x + w <= 1 + 1e-10 && y + h <= 1 + 1e-10);
        assert.ok(w > 0 && h > 0);
        assert.ok(Math.abs(w / h - 2 / 3) < 1e-10);
        assert.ok(Math.abs((handle.includes("w") ? x + w : x) - (handle.includes("w") ? 0.6 : 0.2)) < 1e-10);
        assert.ok(Math.abs((handle.includes("n") ? y + h : y) - (handle.includes("n") ? 0.7 : 0.1)) < 1e-10);
    }
});

test("grid count and browser seek agree with the 24fps decoder grid", () => {
    assert.equal(gridFrames(5.041667), 121);
    assert.equal(gridFrames(10, 0.5), 228);
    assert.ok(Math.abs(seekTime(24, { first_time: 0, duration: 10 }) - (24.5 / 24 - 1e-4)) < 1e-9);
    const t = seekTime(200, { first_time: 0, duration: 20 });
    assert.ok(t >= 501 / 60 && t < 502 / 60);
    assert.ok(seekTime(10000, { duration: 10 }) < 10);
});

test("timeline hit testing and collision-free movement", () => {
    const layout = makeLayout(10, 480, 480);
    assert.equal(hitTest(layout.x(160.5), layout, 100, 107, 60), "marker");
    assert.equal(hitTest(layout.x(120), layout, 100, 107, 60), "window");
    assert.equal(hitTest(layout.x(20), layout, 100, 107, 60), "track");
    assert.equal(hitMarker(layout.x(160.5), layout, 100, [10, 60]), 1);
    assert.equal(moveMarker([10, 20], 0, 20, 107), 19);
    assert.equal(moveMarker([10, 20], 1, 10, 107), 11);
    assert.equal(moveMarker([10, 20], 0, 200, 107), 106);
    assert.equal(suggestFrame([10], 107), 58);
    assert.equal(freeFrame([5, 6], 5, 22), 4);
    assert.equal(freeFrame([0, 1], 0, 2), -1);
});

test("readout and aspect warnings describe visible content", () => {
    assert.equal(describe(240, 107, 60), "00:12.50 · frame 61 of 107 · window 00:10.00–00:14.46 (4.46 s)");
    assert.equal(tickStep(60, 480), 10);
    assert.equal(aspectMismatch(1344, 768, { width: 672, height: 384 }), false);
    assert.equal(aspectMismatch(1024, 1024, { width: 672, height: 384 }), true);
    assert.equal(aspectMismatch(0, 0, { width: 672, height: 384 }), false);
});
