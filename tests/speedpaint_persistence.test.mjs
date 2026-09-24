import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../js/wn_speedpaint.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
const original = { v: 1, width: 1024, height: 1024, background: "#ffffff", painted: false, asset: "original.png", erase_asset: null };
const tick = () => new Promise(resolve => setImmediate(resolve));

async function fixture(t, initial = original) {
    const elements = [], requests = [], snapshots = [], calls = [], timers = new Map();
    let timerId = 0, extension;
    function element(tag) {
        const el = {
            tag, style: {}, classList: { toggle() {} }, listeners: {}, children: [],
            clientWidth: 512, clientHeight: 512,
            append(...children) { this.children.push(...children); },
            setAttribute(name, value) { this[name] = value; },
            addEventListener(name, listener) { this.listeners[name] = listener; },
            removeEventListener() {}, focus() {}, remove() {},
            getBoundingClientRect: () => ({ left: 0, top: 0, width: 512, height: 512 }),
            getContext: () => ({ drawImage() {}, fillRect() {}, putImageData() {} }),
            setPointerCapture() {}, hasPointerCapture: () => false,
            toBlob(callback) { queueMicrotask(() => callback(new Blob(["pixels"]))); },
            toDataURL() { throw new Error("A draft must never encode an inline PNG"); },
        };
        elements.push(el); return el;
    }
    const graph = { id: "graph" };
    const node = {
        graph, properties: {}, inputs: [], size: [430, 575],
        widgets: ["width", "height", "document"].map((name, i) => ({ name, value: i === 2 ? JSON.stringify(initial) : 1024 })),
        addDOMWidget: () => ({}), setDirtyCanvas() {},
    };
    const serialize = () => {
        const info = { widgets_values: node.widgets.map(w => w.value), properties: {} };
        node.onSerialize(info); return JSON.parse(info.widgets_values[2]);
    };
    const app = {
        rootGraph: graph, graph,
        registerExtension(value) { extension = value; },
        extensionManager: {
            workflow: { activeWorkflow: { path: "test.json", changeTracker: { captureCanvasState() { snapshots.push(serialize()); } } } },
            command: { commands: [{ id: "Comfy.SaveWorkflow", function() { calls.push(["save", serialize()]); } }] },
        },
        graphToPrompt() { calls.push(["prompt", serialize()]); },
        loadGraphData() { calls.push(["switch", serialize()]); },
        loadApiJson() { calls.push(["api", serialize()]); },
    };
    const context = vm.createContext({
        app, Blob, FormData, AbortController, URLSearchParams, queueMicrotask,
        setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); },
        requestAnimationFrame: () => 1, cancelAnimationFrame() {},
        ResizeObserver: class { observe() {} disconnect() {} },
        Image: class { set src(value) { queueMicrotask(() => this.onload()); } },
        TileStroke: class {
            constructor() { this.samples = []; this.pointer = 1; this.painted = true; }
            render() {} interpolate() {} finish() { return []; }
        },
        document: { createElement: element, createElementNS: (_, tag) => element(tag), head: { append() {} }, addEventListener() {}, removeEventListener() {} },
        window: { addEventListener() {}, removeEventListener() {} },
        api: {
            apiURL: value => value, addEventListener() {}, removeEventListener() {},
            fetchApi(url, options) { return new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })); },
        },
    });
    vm.runInContext(source, context);
    const editor = context.setupSpeedpaint(node);
    await tick(); await extension.setup();
    t.after(() => node.onRemoved());
    const canvas = elements.find(el => el.tag === "canvas");
    const event = { button: 0, pointerId: 1, pointerType: "mouse", clientX: 100, clientY: 100, preventDefault() {}, stopPropagation() {} };
    return {
        app, node, editor, serialize, snapshots, requests, calls, elements,
        start() { canvas.listeners.pointerdown(event); },
        end() { canvas.listeners.pointerup(event); },
        paint() { this.start(); this.end(); },
        commit(asset = "painted.png") {
            const request = requests.shift(); assert.ok(request, "expected commit request");
            const body = request.options.body;
            const doc = body instanceof FormData ? JSON.parse(body.get("document")) : JSON.parse(body).document;
            delete doc.inline;
            request.resolve({ ok: true, json: async () => ({ ...doc, asset }) });
        },
    };
}

test("drafts stay compact during strokes and delayed commits, then track the saved asset", async t => {
    const f = await fixture(t);
    f.start(); assert.deepEqual(f.serialize(), original); f.end();
    const saving = f.editor.flush(); await tick();
    assert.deepEqual(f.serialize(), original);
    assert.ok(JSON.stringify(f.serialize()).length < 1024);
    f.commit(); await saving;
    assert.equal(f.serialize().asset, "painted.png");
    assert.equal(f.serialize().painted, true);
    assert.equal(f.snapshots.at(-1).asset, "painted.png");
});

for (const method of ["loadGraphData", "loadApiJson", "graphToPrompt", "save"]) {
    test(`${method} waits for the current stroke to reach disk`, async t => {
        const f = await fixture(t); f.start();
        const operation = method === "save" ? f.app.extensionManager.command.commands[0].function : f.app[method];
        const switching = operation(); await tick();
        assert.equal(f.calls.length, 0);
        f.commit(); await switching;
        assert.equal(f.calls.length, 1);
        assert.equal(f.calls[0][1].asset, "painted.png");
    });
}

test("failed commits keep the last durable draft and block workflow switching until retry", async t => {
    const f = await fixture(t); f.paint();
    const switching = f.app.loadGraphData();
    const failed = assert.rejects(switching, /disk full/); await tick();
    f.requests.shift().reject(new Error("disk full")); await failed;
    assert.equal(f.calls.length, 0); assert.deepEqual(f.serialize(), original);
    const retry = f.app.loadGraphData(); await tick(); f.commit(); await retry;
    assert.equal(f.calls[0][1].asset, "painted.png");
});

test("legacy embedded paintings migrate without copying their PNG into drafts", async t => {
    const f = await fixture(t, { ...original, painted: true, inline: "data:image/png;base64," + "A".repeat(6 * 1024 * 1024) });
    assert.ok(JSON.stringify(f.serialize()).length < 1024);
    const saving = f.editor.flush(); await tick();
    assert.ok(JSON.parse(f.requests[0].options.body).document.inline.length > 6 * 1024 * 1024);
    f.commit("migrated.png"); await saving;
    assert.equal(f.serialize().asset, "migrated.png");
    assert.equal(f.serialize().inline, undefined);
});

test("an older commit cannot replace a newer stroke, and flush saves the newer pixels", async t => {
    const f = await fixture(t); f.paint();
    const saving = f.editor.flush(); await tick();
    f.paint(); f.commit("older.png"); await tick();
    assert.equal(f.serialize().asset, "original.png");
    f.commit("newer.png"); await saving;
    assert.equal(f.serialize().asset, "newer.png");
    assert.deepEqual(f.snapshots.map(doc => doc.asset), ["newer.png"]);
});

test("undoing New serializes the restored durable document", async t => {
    const f = await fixture(t);
    const fresh = f.elements.find(el => el.textContent === "New");
    const replacing = fresh.onclick(); await tick(); f.commit("blank.png"); await replacing;
    assert.equal(f.serialize().asset, "blank.png");
    const undo = f.elements.find(el => el["aria-label"] === "Undo · Ctrl/Cmd+Z");
    await undo.onclick();
    assert.equal(f.serialize().asset, "original.png");
    assert.equal(f.snapshots.at(-1).asset, "original.png");
});
