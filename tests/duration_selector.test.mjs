import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import * as helpers from "../js/frame_number.mjs";

const range = (start, step, end) => Array.from({ length: Math.floor((end - start) / step) + 1 }, (_, i) => String(start + i * step));
const PRESETS = {
    "LTX-2.x (8n+1, 24 fps)": { labels: range(9, 8, 481), default: "121", fps: 24 },
    "MiniMax H3 (17n+5, 24 fps)": { labels: range(5, 17, 362), default: "192", fps: 24 },
    "Wan 2.1 / 2.2 14B (4n+1, 16 fps)": { labels: range(5, 4, 161), default: "81", fps: 16 },
};
const LTX = "LTX-2.x (8n+1, 24 fps)", H3 = "MiniMax H3 (17n+5, 24 fps)", WAN = "Wan 2.1 / 2.2 14B (4n+1, 16 fps)";

async function setup() {
    let extension;
    const context = vm.createContext({
        ...helpers,
        app: { registerExtension(value) { extension = value; } },
        api: { fetchApi: () => Promise.resolve({ json: () => Promise.resolve(PRESETS) }) },
        console,
    });
    const source = readFileSync(new URL("../js/wn_frame_number.js", import.meta.url), "utf8")
        .replace(/^import [\s\S]*?;\r?\n/gm, "");
    vm.runInContext(source, context);
    class Node {
        constructor() {
            this.widgets = [
                { name: "preset", value: LTX, options: { values: Object.keys(PRESETS) } },
                { name: "frames", value: "121", options: { values: [] } },
            ];
        }
        addWidget(type, name, value, callback, options) {
            const widget = { type, name, value, callback, options };
            this.widgets.push(widget);
            return widget;
        }
        setDirtyCanvas() {}
    }
    await extension.beforeRegisterNodeDef(Node, { name: "WN_FrameNumberSelector" });
    const node = new Node();
    node.onNodeCreated();
    await settle();
    const get = (name) => node.widgets.find((item) => item.name === name);
    return { node, get };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

async function change(widget, value) {
    widget.value = value;
    widget.callback(value);
    await settle();
}

test("adds a seconds field after frames that is not saved or sent", async () => {
    const { node, get } = await setup();
    assert.deepEqual(node.widgets.map((item) => item.name), ["preset", "frames", "seconds"]);
    assert.equal(get("seconds").value, 5.04);
    assert.equal(get("seconds").serialize, false);
    assert.equal(get("seconds").options.step2, 0.1);
});

test("editing seconds sets the frames, and editing frames sets the seconds", async () => {
    const { get } = await setup();
    await change(get("seconds"), 8);
    assert.equal(get("frames").value, "193");
    assert.equal(get("seconds").value, 8.04);
    await change(get("frames"), "97");
    assert.equal(get("seconds").value, 4.04);
    // The arrows move one legal count even when 0.1 s is less than one step.
    await change(get("preset"), H3);
    assert.equal(get("frames").value, "90");
    await change(get("seconds"), get("seconds").value + 0.1);
    assert.equal(get("frames").value, "107");
    await change(get("seconds"), get("seconds").value - 0.1);
    assert.equal(get("frames").value, "90");
});

test("picking another preset keeps about the same length", async () => {
    const { get } = await setup();
    await change(get("preset"), WAN);
    assert.equal(get("frames").value, "81");
    assert.equal(get("seconds").value, 5.06);
    assert.equal(get("frames").options.values[0], "5");
});

test("loading a workflow keeps the stored frame count", async () => {
    const { node, get } = await setup();
    get("preset").value = WAN;
    get("frames").value = "161";
    node.onConfigure({});
    await settle();
    assert.equal(get("frames").value, "161");
    assert.equal(get("seconds").value, 10.06);
});
