import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { TileStroke } from "../js/speedpaint_stroke.mjs";

// Minimal pixel canvas for the stroke engine's unscaled tile copies.
class PixelCanvas {
    constructor(width = 128, height = 128, colour = [0, 0, 0, 255]) {
        this.width = width; this.height = height;
        this.data = new Uint8ClampedArray(width * height * 4);
        for (let i = 0; i < this.data.length; i += 4) this.data.set(colour, i);
    }
    getContext() { return this; }
    getImageData(x, y, w, h) {
        const data = new Uint8ClampedArray(w * h * 4);
        for (let row = 0; row < h; row++) {
            data.set(this.data.subarray(((y + row) * this.width + x) * 4, ((y + row) * this.width + x + w) * 4), row * w * 4);
        }
        return new ImageData(data, w, h);
    }
    putImageData(image, dx, dy, x = 0, y = 0, w = image.width, h = image.height) {
        for (let row = y; row < y + h; row++) {
            this.data.set(image.data.subarray((row * image.width + x) * 4, (row * image.width + x + w) * 4), ((dy + row) * this.width + dx + x) * 4);
        }
    }
    drawImage(source, x, y, w = source.width, h = source.height, dx = x, dy = y) {
        this.putImageData(source.getImageData(x, y, w, h), dx, dy);
    }
    pixel(x, y) { return [...this.getImageData(x, y, 1, 1).data]; }
}
globalThis.ImageData = class {
    constructor(data, width, height) { Object.assign(this, { data, width, height }); }
};
globalThis.document = { createElement: () => new PixelCanvas() };
const settings = { size: 24, shape: "round", colour: "#ff0000", opacity: 100 };
const point = { x: 128, y: 32, pressure: 1 };
function fixture(opacity = 100, shape = "round") {
    const canvas = new PixelCanvas(256, 64, [200, 100, 50, 255]);
    const backing = new PixelCanvas(256, 64, [200, 100, 50, 255]);
    const base = new PixelCanvas(256, 64, [10, 20, 30, 255]);
    base.data.set([70, 80, 90, 255], (32 * 256 + 130) * 4);
    const stroke = new TileStroke(canvas, canvas, point, { ...settings, opacity, shape }, 1, backing, base);
    return { canvas, backing, base, stroke };
}

for (const shape of ["round", "square"]) test(`${shape} eraser restores original pixels across tile boundaries and preserves untouched pixels`, () => {
    const { canvas, backing, base, stroke } = fixture(100, shape);
    stroke.finish();
    for (const x of [127, 128, 130]) assert.deepEqual(canvas.pixel(x, 32), base.pixel(x, 32));
    assert.deepEqual(canvas.pixel(100, 32), [200, 100, 50, 255]);
    assert.deepEqual(canvas.data, backing.data);
});

test("partial erasing has constant within-stroke opacity, including revisited edges", () => {
    const { canvas, stroke } = fixture(50);
    stroke.render();
    const once = canvas.data.slice();
    for (let i = 0; i < 4; i++) { stroke.stamp(point); stroke.render(); }
    assert.deepEqual(canvas.data, once);
    assert.deepEqual(canvas.pixel(128, 32), [105, 60, 40, 255]);
    stroke.finish();
});

test("cancel restores the exact composition and finished eraser tiles support undo and redo", () => {
    const { canvas, backing, stroke } = fixture();
    const before = canvas.data.slice();
    stroke.render(); stroke.finish(true);
    assert.deepEqual(canvas.data, before);
    assert.deepEqual(backing.data, before);
    const next = fixture();
    const tiles = next.stroke.finish(), after = next.canvas.data.slice();
    for (const tile of tiles) next.canvas.putImageData(tile.before, tile.x, tile.y);
    assert.deepEqual(next.canvas.data, before);
    for (const tile of tiles) next.canvas.putImageData(tile.after, tile.x, tile.y);
    assert.deepEqual(next.canvas.data, after);
});

test("brush painting still uses its colour when no erase target is supplied", () => {
    const canvas = new PixelCanvas(256, 64);
    new TileStroke(canvas, canvas, point, settings, 1, canvas).finish();
    assert.deepEqual(canvas.pixel(128, 32), [255, 0, 0, 255]);
});

const editorSource = readFileSync(new URL("../js/wn_speedpaint.js", import.meta.url), "utf8");
function baseFixture() {
    const calls = [];
    const context = vm.createContext({ abort: new AbortController(), request: async (operation, body) => {
        calls.push({ operation, ...JSON.parse(JSON.stringify(body)) });
        return { asset: `base-${calls.length}.png` };
    } });
    vm.runInContext(editorSource.slice(editorSource.indexOf("    async function eraseAsset("), editorSource.indexOf("    function displayBase(")), context);
    return { calls, eraseAsset: context.eraseAsset };
}
const imported = { v: 1, width: 256, height: 128, background: "#ffffff", source: "original.png", asset: "cropped.png", painted: false, crop: [.2, .5] };

test("unpainted imports use the exact prepared crop and recrop from source when resized", async () => {
    const { calls, eraseAsset } = baseFixture();
    assert.equal(await eraseAsset({ ...imported, erase_asset: "older-crop.png" }), "cropped.png");
    assert.equal(calls.length, 0);
    assert.equal(await eraseAsset(imported, 128, 256), "base-1.png");
    assert.deepEqual(calls[0].document, imported);
    assert.equal(calls[0].width, 128);
    assert.equal(calls[0].height, 256);
});

test("resizing painted artwork transforms its saved eraser base rather than recropping the source", async () => {
    const { calls, eraseAsset } = baseFixture();
    const painted = { ...imported, painted: true, asset: "painting.png", erase_asset: "original-crop.png" };
    assert.equal(await eraseAsset(painted), "original-crop.png");
    assert.equal(calls.length, 0);
    await eraseAsset(painted, 128, 256);
    assert.deepEqual(calls[0].document, { v: 1, background: "#ffffff", painted: true, asset: "original-crop.png" });
});

test("older painted workflows reconstruct an eraser base from the saved source and crop", async () => {
    const { calls, eraseAsset } = baseFixture();
    await eraseAsset({ ...imported, painted: true, asset: "painting.png", inline: "painting recovery" }, 128, 256);
    assert.equal(calls.length, 2);
    assert.equal(calls[0].document.painted, false);
    assert.equal(calls[0].document.source, "original.png");
    assert.deepEqual(calls[0].document.crop, [.2, .5]);
    assert.equal(calls[0].width, 256);
    assert.equal(calls[1].document.asset, "base-1.png");
    assert.equal(calls[1].document.inline, undefined);
});

test("blank canvases use their background without requesting another asset", async () => {
    const { calls, eraseAsset } = baseFixture();
    assert.equal(await eraseAsset({ v: 1, width: 64, height: 64, painted: true, background: "#123456" }), null);
    assert.equal(calls.length, 0);
});
