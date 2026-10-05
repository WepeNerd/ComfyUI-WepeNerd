import assert from "node:assert/strict";
import { test } from "node:test";
import { fillEnclosed } from "../js/mask_stroke.mjs";

// Build an RGBA mask from rows of characters: "#" = full, "+" = half, "-" = faint, "." = empty.
function mask(rows) {
    const height = rows.length, width = rows[0].length, bytes = new Uint8ClampedArray(width * height * 4);
    rows.forEach((row, y) => [...row].forEach((c, x) => {
        const a = { "#": 255, "+": 128, "-": 40, ".": 0 }[c];
        bytes.set([a ? 255 : 0, a ? 255 : 0, a ? 255 : 0, a], (y * width + x) * 4);
    }));
    return { width, height, bytes, alpha: (x, y) => bytes[(y * width + x) * 4 + 3] };
}

test("a closed ring fills solid; the outside stays empty", () => {
    const m = mask([".......", ".#####.", ".#...#.", ".#...#.", ".#####.", "......."]);
    const r = fillEnclosed(m.bytes, m.width, m.height, 1);
    assert.deepEqual(r, { changed: 6, pixelDelta: 6 });
    for (let y = 2; y <= 3; y++) for (let x = 2; x <= 4; x++) assert.equal(m.alpha(x, y), 255);
    assert.equal(m.alpha(0, 0), 0); assert.equal(m.alpha(6, 5), 0);
    assert.deepEqual([...m.bytes.subarray((2 * 7 + 2) * 4, (2 * 7 + 2) * 4 + 4)], [255, 255, 255, 255]);
});

test("a gap in the outline leaks, so nothing fills", () => {
    const m = mask([".......", ".##.##.", ".#...#.", ".#####.", "......."]);
    const copy = m.bytes.slice();
    assert.deepEqual(fillEnclosed(m.bytes, m.width, m.height, 1), { changed: 0, pixelDelta: 0 });
    assert.deepEqual(m.bytes, copy);
});

test("diagonal corners close a loop (fill spreads only 4-connected)", () => {
    const m = mask(["..#..", ".#.#.", "..#.."]);
    fillEnclosed(m.bytes, m.width, m.height, 1);
    assert.equal(m.alpha(2, 1), 255);
});

test("shapes touching the border and multiple shapes", () => {
    const m = mask(["#####......", "#...#.####.", "#####.#..#.", "......####."]);
    const r = fillEnclosed(m.bytes, m.width, m.height, 1);
    assert.equal(r.changed, 5);
    assert.equal(m.alpha(2, 1), 255); assert.equal(m.alpha(7, 2), 255); assert.equal(m.alpha(8, 2), 255);
    assert.equal(m.alpha(5, 1), 0);
});

test("opacity blends like a brush gesture, and a hole inside a hole fills too", () => {
    const m = mask(["#######", "#.....#", "#.###.#", "#.#.#.#", "#.###.#", "#.....#", "#######"]);
    fillEnclosed(m.bytes, m.width, m.height, .5);
    assert.equal(m.alpha(1, 1), 128); assert.equal(m.alpha(3, 3), 128); assert.equal(m.alpha(0, 0), 255);
});

test("the faint inner edge of a soft stroke fills, so no seam is left", () => {
    const m = mask(["#####", "#-..#", "#...#", "#####"]);
    const r = fillEnclosed(m.bytes, m.width, m.height, 1);
    assert.equal(m.alpha(1, 1), 255);
    assert.deepEqual(r, { changed: 6, pixelDelta: 5 });
});

test("a soft stroke's whole inner edge reaches full coverage; its outer edge is untouched", () => {
    // One row through a soft ring: outer ramp, solid centre, inner ramp, interior, mirrored.
    const ramp = [0, 30, 90, 160, 220, 255, 220, 160, 90, 30, 0, 0, 0, 30, 90, 160, 220, 255, 220, 160, 90, 30, 0];
    const width = ramp.length, height = 9, bytes = new Uint8ClampedArray(width * height * 4);
    const set = (x, y, a) => bytes.set([a ? 255 : 0, a ? 255 : 0, a ? 255 : 0, a], (y * width + x) * 4);
    // Rows 0-1 and 7-8 close the ring; rows 2-6 cross it.
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) set(x, y, y === 4 || (y > 1 && y < 7) ? ramp[x] : 255);
    for (let x = 0; x < width; x++) { set(x, 0, 0); set(x, 8, 0); set(x, 1, ramp[x] > 0 ? 255 : 0); set(x, 7, ramp[x] > 0 ? 255 : 0); }
    for (let x = 5; x <= 17; x++) { set(x, 1, 255); set(x, 7, 255); }
    fillEnclosed(bytes, width, height, 1);
    const row = [...Array(width)].map((_, x) => bytes[(4 * width + x) * 4 + 3]);
    assert.deepEqual(row.slice(5, 18), Array(13).fill(255));
    assert.deepEqual(row.slice(0, 5), ramp.slice(0, 5));
    assert.deepEqual(row.slice(18), ramp.slice(18));
});

test("filling never lowers existing coverage", () => {
    const m = mask(["#####", "#...#", "#####"]);
    fillEnclosed(m.bytes, m.width, m.height, .5);
    assert.equal(m.alpha(2, 1), 128); assert.equal(m.alpha(0, 0), 255);
});

test("a low-opacity outline still counts as a wall", () => {
    const m = mask(["+++++", "+...+", "+++++"]);
    fillEnclosed(m.bytes, m.width, m.height, 1);
    assert.equal(m.alpha(2, 1), 255);
});

test("empty masks and zero opacity are no-ops", () => {
    const empty = mask(["...", "..."]);
    assert.deepEqual(fillEnclosed(empty.bytes, 3, 2, 1), { changed: 0, pixelDelta: 0 });
    const ring = mask(["###", "#.#", "###"]);
    assert.deepEqual(fillEnclosed(ring.bytes, 3, 3, 0), { changed: 0, pixelDelta: 0 });
    assert.equal(ring.alpha(1, 1), 0);
});

test("large masks fill quickly", () => {
    const size = 2048, bytes = new Uint8ClampedArray(size * size * 4);
    for (let i = 0; i < size; i++) for (const [x, y] of [[i, 100], [i, size - 100], [100, i], [size - 100, i]])
        if (x >= 100 && x <= size - 100 && y >= 100 && y <= size - 100) bytes[(y * size + x) * 4 + 3] = 255;
    const start = performance.now();
    const r = fillEnclosed(bytes, size, size, 1);
    assert.equal(r.pixelDelta, (size - 201) ** 2);
    assert.ok(performance.now() - start < 2000);
});
