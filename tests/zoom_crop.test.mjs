import assert from "node:assert/strict";
import { test } from "node:test";
import {
    KEEP, POSITIONS, cellOf, keptRect, labelOf, valueAt, valueForKey, valueFromPoint,
} from "../js/zoom_crop.mjs";

// Same construction as POSITIONS in zoom_crop_node.py.
const PYTHON_POSITIONS = ["top", "upper", "center", "lower", "bottom"].flatMap((y) =>
    ["left", "mid-left", "center", "mid-right", "right"].map((x) =>
        [y, x].filter((part) => part !== "center").join(" ") || "center"));

test("positions match the Python node in grid order", () => {
    assert.deepEqual(POSITIONS, PYTHON_POSITIONS);
    assert.equal(POSITIONS[0], "top left");
    assert.equal(POSITIONS[12], "center");
    assert.equal(POSITIONS[24], "bottom right");
});

test("every cell round-trips between value and grid coordinates", () => {
    for (let index = 0; index < 25; index++) {
        const { column, row } = cellOf(POSITIONS[index]);
        assert.equal(row * 5 + column, index);
        assert.equal(valueAt(column, row), POSITIONS[index]);
    }
    assert.deepEqual(cellOf("not a position"), { column: 2, row: 2 });
});

test("a click anywhere in a cell picks that cell; drags past the edge clamp", () => {
    for (let index = 0; index < 25; index++) {
        const x = ((index % 5) + 0.5) * 35.2, y = (Math.floor(index / 5) + 0.5) * 26.4;
        assert.equal(valueFromPoint(x, y, 176, 132), POSITIONS[index]);
    }
    assert.equal(valueFromPoint(0, 0, 176, 132), "top left");
    assert.equal(valueFromPoint(176, 132, 176, 132), "bottom right");
    assert.equal(valueFromPoint(-40, 300, 176, 132), "bottom left");
    assert.equal(valueFromPoint(10, 10, 0, 132), null);
});

test("kept box hugs the matching edge and centers in the middle", () => {
    assert.deepEqual(keptRect("top left"), { left: 0, top: 0, width: KEEP, height: KEEP });
    const corner = keptRect("bottom right");
    assert.ok(Math.abs(corner.left + corner.width - 1) < 1e-9);
    assert.ok(Math.abs(corner.top + corner.height - 1) < 1e-9);
    const middle = keptRect("center");
    assert.ok(Math.abs(middle.left + middle.width / 2 - 0.5) < 1e-9);
});

test("arrow keys nudge and stop at the edges; C and Home recenter", () => {
    assert.equal(valueForKey("center", "ArrowLeft"), "mid-left");
    assert.equal(valueForKey("center", "ArrowUp"), "upper");
    assert.equal(valueForKey("top left", "ArrowLeft"), "top left");
    assert.equal(valueForKey("bottom right", "ArrowDown"), "bottom right");
    assert.equal(valueForKey("lower mid-right", "ArrowRight"), "lower right");
    assert.equal(valueForKey("top left", "c"), "center");
    assert.equal(valueForKey("top left", "Home"), "center");
    assert.equal(valueForKey("center", "Enter"), null);
});

test("labels are readable", () => {
    assert.equal(labelOf("lower mid-right"), "Lower Mid-Right");
    assert.equal(labelOf("bogus"), "Center");
});
