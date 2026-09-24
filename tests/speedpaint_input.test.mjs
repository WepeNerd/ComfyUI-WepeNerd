import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { loadConnectedImage } from "../js/speedpaint_input.mjs";

function fixture() {
    const calls = [], graph = { nodes: [] }, signal = new AbortController();
    const source = { id: 1, graph, widgets: [{
        beforeQueued(options) { assert.equal(options.isPartialExecution, true); calls.push("before"); },
        afterQueued(options) { assert.equal(options.isPartialExecution, true); calls.push("after"); },
    }] };
    const node = { id: 2, graph, widgets: [{ beforeQueued() { assert.fail("editor hook ran"); } }] };
    graph.nodes = [source, node, { id: 3, widgets: [{ beforeQueued() { assert.fail("downstream hook ran"); } }] }];
    let sink;
    const app = { graph, nodeOutputs: { 1: { images: [{ filename: "stale.png" }] } }, async graphToPrompt() {
        calls.push("serialize");
        return { output: {
            1: { class_type: "Source", inputs: { seed: calls.includes("before") ? 42 : 41 } },
            2: { class_type: "WN_Speedpaint", inputs: { image: ["1", 1], width: 64, height: 64 } },
            3: { class_type: "SaveImage", inputs: { images: ["2", 0] } },
        } };
    } };
    const blob = new Blob(["image"]);
    const api = {
        async queuePrompt(position, prompt) {
            calls.push("queue");
            sink = Object.keys(prompt.output).find(key => key.startsWith("wn_speedpaint_snapshot_"));
            assert.deepEqual(Object.keys(prompt.output).sort(), ["1", sink].sort());
            assert.deepEqual(prompt.output[sink].inputs.image, ["1", 1]);
            assert.equal(prompt.output[1].inputs.seed, 42);
            return { prompt_id: "job" };
        },
        async fetchApi(url) {
            if (url.startsWith("/history/")) return { ok: true, json: async () => ({ job: {
                status: { completed: true }, outputs: { [sink]: { images: [{ filename: "fresh.png", type: "input", subfolder: "snapshots" }] } },
            } }) };
            assert.match(url, /filename=fresh.png/);
            return { ok: true, blob: async () => blob };
        },
    };
    return { node, app, api, signal, calls, blob };
}

test("Load queues only IMAGE ancestors on every click, even with a cached preview", async () => {
    const f = fixture();
    for (let click = 0; click < 2; click++) assert.equal(await loadConnectedImage(f.node, f.app, f.api, f.signal.signal), f.blob);
    assert.deepEqual(f.calls, Array(2).fill(["serialize", "before", "serialize", "queue", "after"]).flat());
});

test("upstream errors preserve the editor by rejecting the import", async () => {
    const f = fixture();
    f.api.fetchApi = async () => ({ ok: true, json: async () => ({ job: { status: { status_str: "error" } } }) });
    await assert.rejects(loadConnectedImage(f.node, f.app, f.api, f.signal.signal), /failed or was interrupted/);
});

test("missing snapshot is reported instead of replacing the canvas", async () => {
    const f = fixture();
    f.api.fetchApi = async () => ({ ok: true, json: async () => ({ job: { status: { completed: true } } }) });
    await assert.rejects(loadConnectedImage(f.node, f.app, f.api, f.signal.signal), /returned no image/);
});

test("removed editors abort polling and release the wait", async () => {
    const f = fixture();
    f.api.fetchApi = async () => {
        setTimeout(() => f.signal.abort(), 10);
        return { ok: true, json: async () => ({}) };
    };
    await assert.rejects(loadConnectedImage(f.node, f.app, f.api, f.signal.signal), { name: "AbortError" });
});

async function editorFixture() {
    const f = fixture(), elements = [];
    const document = { v: 1, asset: "old.png", width: 64, height: 64, painted: true, background: "#ffffff" };
    function element(tag) {
        const el = { tag, style: {}, classList: { toggle() {} }, clientWidth: 400, clientHeight: 400,
            setAttribute() {}, append() {}, addEventListener() {}, removeEventListener() {}, remove() {},
            click() { this.clicks = (this.clicks || 0) + 1; },
            getContext() { return { drawImage() {}, fillRect() {} }; },
        };
        elements.push(el); return el;
    }
    Object.assign(f.node, { size: [430, 575], inputs: [{ name: "image", link: 1 }],
        widgets: [ { name: "width", value: 64 }, { name: "height", value: 64 }, { name: "document", value: JSON.stringify(document) } ],
        addDOMWidget() { return {}; }, setDirtyCanvas() {},
    });
    f.app.extensionManager = { workflow: { activeWorkflow: { changeTracker: { captureCanvasState() {} } } } };
    f.app.registerExtension = () => {};
    const fetchApi = f.api.fetchApi;
    Object.assign(f.api, { apiURL: url => url, addEventListener() {}, removeEventListener() {}, async fetchApi(url, options) {
        if (url === "/wepenerd/speedpaint/import") {
            assert.equal(options.body.get("width"), "64");
            return { ok: true, json: async () => ({ ...document, asset: "new.png", source: "new.png", painted: false }) };
        }
        return fetchApi(url, options);
    } });
    const context = vm.createContext({ app: f.app, api: f.api, loadConnectedImage,
        document: { createElement: element, createElementNS: (_, tag) => element(tag), head: { append() {} }, addEventListener() {}, removeEventListener() {} },
        window: { addEventListener() {}, removeEventListener() {} },
        ResizeObserver: class { observe() {} disconnect() {} },
        Image: class { set src(value) { queueMicrotask(() => this.onload()); } },
        AbortController, FormData, URLSearchParams, queueMicrotask, setTimeout, clearTimeout,
    });
    const source = readFileSync(new URL("../js/wn_speedpaint.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
    vm.runInContext(source, context);
    const editor = context.setupSpeedpaint(f.node);
    await new Promise(resolve => setImmediate(resolve));
    await editor.flush();
    f.app.graphToPrompt = context.prepareOperation(f.app.graphToPrompt, true);
    return { ...f, elements, data: f.node.widgets[2], load: elements.find(el => el.textContent === "Load"), file: elements.find(el => el.type === "file") };
}

test("Load button imports upstream through queue/save synchronization and can undo", { timeout: 2000 }, async () => {
    const f = await editorFixture();
    await f.load.onclick();
    assert.equal(JSON.parse(f.data.value).source, "new.png");
    assert.equal(f.file.clicks, undefined);
    await f.elements.find(el => el.title === "Undo · Ctrl/Cmd+Z").onclick();
    assert.equal(JSON.parse(f.data.value).asset, "old.png");
    f.node.onRemoved();
});

test("disconnected Load opens the file picker without queueing", async () => {
    const f = await editorFixture();
    f.node.inputs[0].link = null;
    await f.load.onclick();
    assert.equal(f.file.clicks, 1);
    assert.deepEqual(f.calls, []);
    f.node.onRemoved();
});

test("failed upstream Load keeps the painting and allows a successful retry", async () => {
    const f = await editorFixture(), fetchApi = f.api.fetchApi;
    f.api.fetchApi = async () => { throw new Error("Connection lost"); };
    await f.load.onclick();
    assert.equal(JSON.parse(f.data.value).asset, "old.png");
    assert.equal(f.load.disabled, false);
    f.api.fetchApi = fetchApi;
    await f.load.onclick();
    assert.equal(JSON.parse(f.data.value).source, "new.png");
    f.node.onRemoved();
});
