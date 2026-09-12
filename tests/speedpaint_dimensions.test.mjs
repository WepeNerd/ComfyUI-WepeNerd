import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../js/wn_speedpaint.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace(/^export /gm, "");
const context = vm.createContext({
    app: { registerExtension() {} },
    document: { createElement: () => ({}), head: { append() {} } },
});
vm.runInContext(source, context);
const { inputDimension } = context;

function node(type, values = {}) {
    return {
        type,
        widgets: Object.entries(values).map(([name, value]) => ({ name, value })),
        inputs: [],
        getInputLink(slot) { return this.inputs[slot].connection; },
        getInputNode(slot) { return this.inputs[slot].source; },
    };
}
function connect(target, name, source, origin_slot) {
    target.inputs.push({ name, link: 1, source, connection: { origin_slot } });
}
function pair() {
    const drag = node("WN_DragResolution", { width: 1536, height: 864 });
    const paint = node("WN_Speedpaint", { width: 1024, height: 1024 });
    connect(paint, "width", drag, 0);
    connect(paint, "height", drag, 1);
    return { drag, paint };
}

test("uses current Drag Resolution values before any execution", () => {
    const { drag, paint } = pair();
    assert.equal(inputDimension(paint, "width"), 1536);
    assert.equal(inputDimension(paint, "height"), 864);
    drag.widgets[0].value = 768;
    drag.widgets[1].value = 1344;
    assert.equal(inputDimension(paint, "width"), 768);
    assert.equal(inputDimension(paint, "height"), 1344);
});

test("follows the connected output slot, including swapped dimensions", () => {
    const { paint } = pair();
    paint.inputs[0].connection.origin_slot = 1;
    paint.inputs[1].connection.origin_slot = 0;
    assert.equal(inputDimension(paint, "width"), 864);
    assert.equal(inputDimension(paint, "height"), 1536);
});

test("follows reroutes without losing the original output slot", () => {
    const { drag } = pair();
    const first = node("Reroute"), second = node("Reroute");
    connect(first, "", drag, 1);
    connect(second, "", first, 0);
    const paint = node("WN_Speedpaint");
    connect(paint, "width", second, 0);
    assert.equal(inputDimension(paint, "width"), 864);
});

test("reads INT primitives and retains local values for disconnected axes", () => {
    const { paint } = pair();
    paint.inputs[0].link = null;
    paint.inputs[1].source = node("PrimitiveNode", { value: 640 });
    paint.inputs[1].connection.origin_slot = 0;
    assert.equal(inputDimension(paint, "width"), 1024);
    assert.equal(inputDimension(paint, "height"), 640);
});

test("does not mistake backend input widgets for computed output values", () => {
    const { paint } = pair();
    paint.inputs[0].source = node("SomeCalculation", { width: 256 });
    assert.equal(inputDimension(paint, "width"), undefined);
});

test("unresolved and cyclic connections retain the execution-preview fallback", () => {
    const { paint } = pair();
    paint.inputs[0].source = null;
    assert.equal(inputDimension(paint, "width"), undefined);
    const reroute = node("Reroute");
    connect(reroute, "", reroute, 0);
    paint.inputs[0].source = reroute;
    assert.equal(inputDimension(paint, "width"), undefined);
});
