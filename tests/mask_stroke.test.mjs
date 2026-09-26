import assert from "node:assert/strict";
import { test } from "node:test";
import { MaskStroke, brushCoverage, maskSettings } from "../js/mask_stroke.mjs";

function raster(width = 93, height = 61, initial = 0) {
    const data = new Uint8ClampedArray(width * height * 4);
    for (let i = 0; i < data.length; i += 4) { data.fill(255, i, i + 3); data[i + 3] = initial; }
    let reads = 0;
    const read = (x, y, w, h) => {
        reads++;
        const tile = new Uint8ClampedArray(w * h * 4);
        for (let row = 0; row < h; row++) tile.set(data.subarray(((y + row) * width + x) * 4, ((y + row) * width + x + w) * 4), row * w * 4);
        return tile;
    };
    const write = (x, y, w, h, bytes) => {
        for (let row = 0; row < h; row++) data.set(bytes.subarray(row * w * 4, (row + 1) * w * 4), ((y + row) * width + x) * 4);
    };
    return { width, height, data, write, alpha: (x, y) => data[(y * width + x) * 4 + 3] / 255,
        reads: () => reads, stroke: settings => new MaskStroke(width, height, read, settings) };
}
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) <= 1 / 255 + 1e-7, `${actual} != ${expected}`);

test("opacity is per gesture: stationary/revisited 50% stays .5; separate gestures give .75", () => {
    const r = raster(), point = { x: 40.5, y: 30.5 };
    for (const expected of [.5, .75]) {
        const stroke = r.stroke({ size: 20, opacity: 50 });
        for (let i = 0; i < 30; i++) { stroke.segment(point); stroke.flush(r.write); }
        near(r.alpha(40, 30), expected);
    }
});

test("soft erasure uses initial alpha times one minus gesture coverage/opacity", () => {
    const r = raster(93, 61, 204), point = { x: 40.5, y: 30.5 };
    const stroke = r.stroke({ size: 30, opacity: 50, softness: 100, erase: true });
    for (let i = 0; i < 20; i++) stroke.segment(point);
    stroke.flush(r.write);
    near(r.alpha(40, 30), .4);
    near(r.alpha(50, 30), .8 * (1 - .5 * brushCoverage(10, 15, 1)));
    near(r.alpha(60, 30), .8);
});

test("sparse, dense and frame/coalesced batches are identical for the same polyline, including corners", () => {
    const path = [{ x: -7, y: 4 }, { x: 72, y: 13 }, { x: 72, y: 52 }, { x: 9, y: 47 }];
    for (const softness of [0, 1, 50, 100]) {
        const outputs = [];
        for (const [subdivisions, flushEach] of [[1, false], [1, true], [37, true], [37, false]]) {
            const r = raster(), stroke = r.stroke({ size: 17, opacity: 63, softness });
            stroke.segment(path[0]);
            for (let j = 1; j < path.length; j++) {
                let last = path[j - 1];
                for (let i = 1; i <= subdivisions; i++) {
                    const t = i / subdivisions, a = path[j - 1], b = path[j];
                    const point = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
                    stroke.segment(last, point); last = point;
                    if (flushEach) stroke.flush(r.write);
                }
            }
            stroke.flush(r.write); outputs.push(r.data);
        }
        for (const data of outputs.slice(1)) assert.deepEqual(data, outputs[0]);
    }
});

test("hardness endpoints, one-pixel click, flat center and smooth falloff remain finite", () => {
    assert.equal(brushCoverage(0, .5, 0), 1);
    assert.equal(brushCoverage(1, .5, 0), 0);
    assert.equal(brushCoverage(2, 10, .5), 1);
    for (const softness of [0, .01, .5, 1]) {
        let previous = 1;
        for (let distance = 0; distance < 30; distance += .125) {
            const c = brushCoverage(distance, 10, softness);
            assert.ok(Number.isFinite(c) && c >= 0 && c <= previous); previous = c;
        }
        const r = raster(), stroke = r.stroke({ size: 1, softness: softness * 100 });
        stroke.segment({ x: 20.5, y: 20.5 }); stroke.flush(r.write);
        assert.equal(r.alpha(20, 20), 1); assert.equal(r.alpha(21, 20), 0);
    }
});

test("large and fast brushes cross tiles, clip boundaries and preserve untouched pixels", () => {
    const r = raster(513, 257), stroke = r.stroke({ size: 512 });
    stroke.segment({ x: -100, y: -100 }, { x: 600, y: -100 }); stroke.flush(r.write);
    assert.equal(r.alpha(0, 0), 1); assert.equal(r.alpha(512, 0), 1);
    assert.equal(r.alpha(256, 256), 0);
    const before = r.data.slice();
    const outside = r.stroke({ size: 12 }); outside.segment({ x: -80, y: -80 }); outside.flush(r.write);
    assert.equal(outside.changed, false); assert.deepEqual(r.data, before);
});

test("zero opacity allocates no tiles; black erasure and full-white paint create no changes", () => {
    for (const [initial, settings] of [[0, { opacity: 0 }], [0, { erase: true }], [255, {}]]) {
        const r = raster(93, 61, initial), stroke = r.stroke(settings);
        stroke.segment({ x: 30, y: 30 });
        assert.equal(stroke.flush(r.write), false); assert.equal(stroke.changed, false);
        if (settings.opacity === 0) assert.equal(r.reads(), 0);
    }
});

test("rectangle ignores softness, uses gesture opacity, and area-antialiases fractional edges", () => {
    const r = raster(), stroke = r.stroke({ opacity: 50, softness: 100 });
    stroke.rectangle({ x: 21.5, y: 10 }, { x: 8, y: 20 }); stroke.flush(r.write);
    near(r.alpha(12, 12), .5); near(r.alpha(21, 12), .25);
    assert.equal(r.alpha(22, 12), 0);
    const zero = r.stroke(); zero.rectangle({ x: 1, y: 1 }, { x: 1, y: 1 });
    assert.equal(zero.changed, false);
});

test("cancellation restores every touched tile byte-for-byte; stored RGB never contains tint", () => {
    const r = raster(257, 133, 83), before = r.data.slice();
    const stroke = r.stroke({ opacity: 40, softness: 80 });
    stroke.segment({ x: 1, y: 1 }, { x: 252, y: 129 }); stroke.flush(r.write);
    for (let i = 0; i < r.data.length; i += 4) assert.deepEqual([...r.data.slice(i, i + 3)], [255, 255, 255]);
    stroke.cancel(r.write); assert.deepEqual(r.data, before);
});

test("legacy settings default to hard/opaque; duplicates own independent settings and references", () => {
    const legacy = { brush: 83, reference: { asset: { filename: "reference.png" } } };
    const a = maskSettings(legacy), b = maskSettings(legacy, true);
    assert.deepEqual([a.brush, a.softness, a.opacity, a.grayscale, a.open], [83, 0, 100, false, false]);
    assert.equal(b.open, true); b.opacity = 20; b.reference.asset.filename = "other.png";
    assert.equal(a.opacity, 100); assert.equal(legacy.reference.asset.filename, "reference.png");
    assert.deepEqual([maskSettings({ softness: Infinity }).softness, maskSettings({ opacity: -5 }).opacity], [0, 0]);
});

test("pixelDelta tracks exact nonzero coverage through paint and erase without a full scan", () => {
    const r = raster(), count = () => r.data.filter((v, i) => i % 4 === 3 && v > 0).length;
    for (const erase of [false, false, true, true]) {
        const before = count(), stroke = r.stroke({ size: 50, softness: 80, opacity: 100, erase });
        stroke.polyline([{ x: 20, y: 20 }, { x: 70, y: 20 }, { x: 70, y: 50 }, { x: 20, y: 20 }]);
        stroke.flush(r.write);
        assert.equal(count() - before, stroke.pixelDelta);
    }
});

test("coalesced straight samples merge exactly while corners and reversals retain all pixels", () => {
    const path = [{x:5,y:20},{x:6,y:20},{x:7,y:20},{x:40,y:20},{x:40,y:20},{x:25,y:20},{x:25,y:40},{x:28,y:37}];
    for (const erase of [false, true]) {
        const a = raster(93,61,102), b = raster(93,61,102);
        const sa = a.stroke({size:40,softness:100,opacity:68,erase}), sb = b.stroke({size:40,softness:100,opacity:68,erase});
        for (let i=0;i<path.length;i++) sa.segment(path[Math.max(0,i-1)],path[i]);
        sa.flush(a.write);
        let segments = 0; const segment = sb.segment.bind(sb);
        sb.segment = (...args) => { segments++; segment(...args); };
        sb.polyline(path); sb.flush(b.write);
        assert.equal(segments, 4);
        assert.deepEqual(a.data,b.data);
    }
});
