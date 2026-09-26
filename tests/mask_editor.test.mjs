import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { MaskStroke, maskSettings } from "../js/mask_stroke.mjs";

const source = readFileSync(new URL("../js/wn_masked_lora.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
const tick = () => new Promise(resolve => setImmediate(resolve));
const png = alpha => `data:image/png;base64,${alpha}`;

async function fixture(maskOnly, saved = { v: 1, width: 1536, height: 1024, png: png(255) }) {
    const elements = [], imageReads = [], frames = new Map();
    const images = { fail: false };
    const counts = { encodes: 0, reads: 0, resizes: 0, draws: 0, allocations: 0 };
    let frameId = 0;
    function element(tag) {
        const el = {
            tag, style: {}, children: [], alpha: 0,
            append(...children) { this.children.push(...children); },
            setAttribute(k, v) { this[k] = v; }, getAttribute(k) { return this[k]; },
            addEventListener() {}, remove() {}, focus() {},
            click() { return this.onclick?.(); },
            getBoundingClientRect: () => ({ left: 0, top: 0, width: 376, height: 251 }),
            toDataURL() { counts.encodes++; return png(this.alpha); },
        };
        if (tag === "canvas") {
            counts.allocations++;
            for (const key of ["width", "height"]) {
                let size = 300;
                Object.defineProperty(el, key, { get: () => size, set: value => { size = value; counts.resizes++; el.alpha = 0; } });
            }
        }
        el.getContext = () => ({
            clearRect() { el.alpha = 0; }, drawImage(image) { counts.draws++; el.alpha = image.alpha ?? 255; },
            getImageData: () => { counts.reads++; return { data: [255, 255, 255, el.alpha] }; },
            fillRect() {}, save() {}, restore() {}, translate() {}, beginPath() {}, arc() {}, stroke() {},
        });
        elements.push(el); return el;
    }
    const defaults = { strength: -.7, lora_name: "fixture.safetensors", start_percent: 0, end_percent: 1, apply_to: "Both" };
    const values = maskOnly ? ["mask_data"] : ["lora_name", "strength", "mask_data", "start_percent", "end_percent", "apply_to"];
    const node = {
        properties: { wnMask: { v: 1, open: true, brush: 60 } }, size: [400, 500], inputs: [],
        widgets: values.map(name => ({ name, value: name === "mask_data" ? (saved ? JSON.stringify(saved) : "") : defaults[name] })),
        graph: { change() {} }, setSize() {}, computeSize: () => [400, 500], setDirtyCanvas() {},
        addDOMWidget: () => ({ options: {} }),
    };
    const context = vm.createContext({
        document: { createElement: element, createElementNS: (_, tag) => element(tag), head: { append() {} } },
        window: { devicePixelRatio: 1, addEventListener() {}, removeEventListener() {} },
        app: { registerExtension() {} }, api: {}, queueMicrotask, MaskStroke, maskSettings,
        requestAnimationFrame: callback => { frames.set(++frameId, callback); return frameId; },
        cancelAnimationFrame: id => frames.delete(id),
        Image: class { set src(value) { imageReads.push(value); this.alpha = Number(value?.split(",")[1]); queueMicrotask(() => images.fail ? this.onerror() : this.onload()); } },
    });
    vm.runInContext(source, context);
    context.setupMaskedLora(node, { maskOnly });
    await tick();
    const button = name => elements.find(e => e["aria-label"] === name);
    const state = () => elements.find(e => e.className === "wn-mask-state").textContent;
    const data = () => node.widgets.find(w => w.name === "mask_data");
    const flush = () => { const pending = [...frames.values()]; frames.clear(); for (const callback of pending) callback(); };
    flush();
    return { node, button, state, data, imageReads, images, elements, counts, flush, frames, window: context.window };
}

for (const maskOnly of [false, true]) {
    const name = maskOnly ? "Paint Mask" : "Load LoRA Masked";
    test(`${name}: failed Undo preserves painting and history, reports the error, and permits retry`, async () => {
        const f = await fixture(maskOnly);
        f.button("Clear mask").click();
        const cleared = f.data().value;
        f.images.fail = true;
        for (let i = 0; i < 2; i++) {
            await assert.doesNotReject(() => f.button("Undo").click());
            assert.equal(f.data().value, cleared);
            assert.equal(f.state(), "Empty");
            assert.equal(f.button("Undo").disabled, false);
            assert.ok(f.elements.some(e => e.textContent?.startsWith("Cannot undo: Could not decode image")));
        }
        f.images.fail = false;
        await f.button("Undo").click();
        assert.equal(JSON.parse(f.data().value).png, png(255));
        assert.equal(f.button("Undo").disabled, true);
        assert.ok(!f.elements.some(e => e.textContent?.startsWith("Cannot undo:")));
    });
    test(`${name}: Clear keeps dimensions; Undo restores paint; reload accepts empty marker`, async () => {
        const f = await fixture(maskOnly);
        assert.equal(f.state(), "Painted");
        f.button("Clear mask").click();
        const cleared = JSON.parse(f.data().value);
        assert.deepEqual({ v: cleared.v, width: cleared.width, height: cleared.height, empty: cleared.empty },
            { v: 1, width: 1536, height: 1024, empty: true });
        assert.equal(cleared.png, undefined);
        assert.equal(f.state(), "Empty");
        assert.equal(f.button("Clear mask").disabled, true);
        await f.button("Undo").click();
        assert.equal(f.state(), "Painted");
        assert.equal(JSON.parse(f.data().value).png, png(255));
        const restored = await fixture(maskOnly, cleared);
        assert.equal(restored.state(), "Empty");
        assert.equal(restored.imageReads.includes(undefined), false);
        assert.equal(restored.button("Clear mask").disabled, true);
    });

    test(`${name}: legacy empty state and MASK override preserve painting`, async () => {
        const empty = await fixture(maskOnly, null);
        assert.equal(empty.state(), "Empty");
        const f = await fixture(maskOnly);
        const saved = f.data().value;
        f.node.inputs.push({ name: "mask", link: 123 });
        f.node.onConnectionsChange();
        assert.equal(f.state(), "Using MASK input");
        assert.equal(f.data().value, saved);
        f.node.inputs[0].link = null;
        f.node.onConnectionsChange();
        assert.equal(f.state(), "Painted");
    });
}

test("legacy LoRA positional widget values survive configure and Clear", async () => {
    const f = await fixture(false);
    f.node.onConfigure({ widgets_values: f.node.widgets.map(w => w.value) });
    await tick();
    f.button("Clear mask").click();
    assert.deepEqual(f.node.widgets.map(w => w.name), ["lora_name", "strength", "mask_data", "start_percent", "end_percent", "apply_to"]);
    assert.deepEqual(f.node.widgets.slice(0, 2).map(w => w.value), ["fixture.safetensors", -.7]);
    assert.equal(JSON.parse(f.node.widgets[2].value).width, 1536);
});

test("old positional arrays with/without editor placeholder migrate to full range/Both", async () => {
    for (const placeholder of [[], [""], [null]]) {
        const f = await fixture(false);
        const old = ["old.safetensors", -.4, f.data().value, ...placeholder];
        f.node.widgets.forEach((w, i) => { w.value = old[i]; }); // Native configure assigns before onConfigure.
        f.node.onConfigure({ widgets_values: old }); await tick();
        assert.deepEqual(f.node.widgets.map(w => w.value), [...old.slice(0, 3), 0, 1, "Both"]);
        assert.equal(f.state(), "Painted");
    }
});

test("new schedule round trip and disabled/invalid range status preserve mask and fields", async () => {
    const f = await fixture(false), values = ["new.safetensors", -.8, f.data().value, .2, .7, "Positive only", ""];
    f.node.widgets.forEach((w, i) => { w.value = values[i]; });
    f.node.onConfigure({ widgets_values: values }); await tick();
    const info = { widgets_values: f.node.widgets.map(w => w.value), properties: {} };
    f.node.onSerialize(info);
    assert.deepEqual(info.widgets_values, values.slice(0, 6));
    const end = f.node.widgets.find(w => w.name === "end_percent");
    end.value = .2; end.callback();
    assert.ok(f.elements.some(e => e.textContent === "Adapter disabled: start and end are equal."));
    end.value = .1; end.callback();
    assert.ok(f.elements.some(e => e.textContent === "Invalid range: start must not exceed end."));
    assert.equal(f.data().value, values[2]);
    assert.equal((await fixture(true)).node.widgets.length, 1);
});

test("100 idle pointer moves only draw the cursor; frames coalesce and removal cancels them", async () => {
    const f = await fixture(false);
    const canvas = f.elements.find(e => e.className === "wn-mask-canvas");
    const before = { ...f.counts };
    for (let i = 0; i < 100; i++) {
        canvas.onpointermove({ clientX: i, clientY: i }); f.flush();
    }
    assert.deepEqual(f.counts, before);
    for (let i = 0; i < 100; i++) canvas.onpointermove({ clientX: i, clientY: i });
    assert.equal(f.frames.size, 1);
    f.node.onRemoved(); assert.equal(f.frames.size, 0);
});

test("brush settings migrate without backend widgets and rectangle disables brush-only controls", async () => {
    const f = await fixture(true);
    assert.equal(f.node.properties.wnMask.opacity, 100);
    assert.equal(f.node.properties.wnMask.softness, 0);
    f.button("Opacity").value = 50; f.button("Opacity").oninput();
    f.button("Softness").value = 75; f.button("Softness").oninput();
    await f.button("Rectangle").onclick();
    assert.equal(f.button("Softness").disabled, true); assert.equal(f.button("Opacity").disabled, undefined);
    f.button("Grayscale mask").onclick();
    assert.equal(f.node.properties.wnMask.grayscale, true);
    assert.deepEqual(f.node.widgets.map(w => w.name), ["mask_data"]);
});

test("DPR/display resizing changes only display surfaces, preserving native brush and serialized coverage", async () => {
    const f = await fixture(false), before = f.data().value;
    const canvas = f.elements.find(e => e.className === "wn-mask-canvas"), width = canvas.width;
    f.window.devicePixelRatio = 2; f.node.onResize(); f.flush();
    assert.equal(canvas.width, width * 2);
    assert.equal(f.node.properties.wnMask.brush, 60);
    assert.equal(f.data().value, before);
    const settled = { ...f.counts };
    canvas.onpointermove({ clientX: 80, clientY: 100 }); f.flush();
    assert.deepEqual(f.counts, settled);
});

test("a later configure wins over an older asynchronous PNG restore", async () => {
    for (const maskOnly of [false, true]) {
        const f = await fixture(maskOnly);
        f.data().value = JSON.stringify({ v: 1, width: 800, height: 600, png: png(64) }); f.node.onConfigure();
        f.data().value = JSON.stringify({ v: 1, width: 640, height: 320, png: png(173) }); f.node.onConfigure();
        await tick(); f.flush();
        const final = JSON.parse(f.data().value);
        assert.equal(final.width, 640); assert.equal(final.height, 320); assert.equal(final.png, png(173));
    }
});
