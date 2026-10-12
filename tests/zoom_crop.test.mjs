import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function setup() {
    let extension;
    const context = vm.createContext({ app: { registerExtension(value) { extension = value; } } });
    const source = readFileSync(new URL("../js/wn_zoom_crop.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "");
    vm.runInContext(source, context);
    const values = ["top", "upper", "center", "lower", "bottom"].flatMap(y =>
        ["left", "mid-left", "center", "mid-right", "right"].map(x =>
            [y, x].filter(part => part !== "center").join(" ") || "center"));
    const changes = [];
    const widget = { name: "position", type: "combo", value: "center", options: { values },
        callback(value) { changes.push(value); } };
    class Node {
        constructor() {
            this.widgets = [{ name: "crop_width", value: 0 }, { name: "crop_height", value: 0 }, widget];
            this.graph = { beforeChange() { changes.push("before"); }, afterChange() { changes.push("after"); } };
        }
        onNodeCreated() { this.created = true; }
        computeSize() { return [220, 260]; }
        setSize(size) { this.size = size; }
        setDirtyCanvas() { this.dirty = true; }
    }
    extension.beforeRegisterNodeDef(Node, { name: "WN_ZoomCrop" });
    const node = new Node();
    node.onNodeCreated();
    const ctx = { save() {}, restore() {}, fillRect() {}, beginPath() {}, arc() {}, fill() {}, fillText() {} };
    widget.draw(ctx, node, 220, 80);
    return { node, widget, values, changes, ctx };
}

test("all 25 cells select and serialize the corresponding position", () => {
    const { node, widget, values, changes } = setup();
    assert.ok(node.created);
    assert.equal(node.widgets.length, 3);
    assert.equal(widget.serializeValue(), "center");
    for (let index = 0; index < 25; index++) {
        changes.length = 0;
        assert.equal(widget.mouse({ type: "pointerdown", button: 0 },
            [40 + (index % 5 + 0.5) * 28, 86 + (Math.floor(index / 5) + 0.5) * 28], node), true);
        assert.equal(widget.serializeValue(), values[index]);
        assert.deepEqual(changes, ["before", values[index], "after"]);
    }
    assert.ok(node.dirty);
});

test("ignores clicks outside grid, right clicks, and pointer movement", () => {
    const { node, widget, changes } = setup();
    for (const pos of [[39, 100], [180, 100], [60, 85], [60, 226]]) {
        assert.equal(widget.mouse({ type: "pointerdown" }, pos, node), false);
    }
    assert.equal(widget.mouse({ type: "pointerdown", button: 2 }, [50, 100], node), false);
    assert.equal(widget.mouse({ type: "pointermove" }, [50, 100], node), false);
    assert.deepEqual(changes, []);
    assert.equal(widget.value, "center");
});

test("restored widget value redraws and serializes without resetting", () => {
    const { node, widget, ctx } = setup();
    widget.value = JSON.parse(JSON.stringify("bottom right"));
    node.onNodeCreated();
    widget.draw(ctx, node, 300, 100);
    assert.equal(widget.serializeValue(), "bottom right");
    assert.equal(node.widgets.length, 3);
    widget.mouse({ type: "mousedown", button: 0 }, [94, 120], node);
    assert.equal(widget.serializeValue(), "top left");
});
