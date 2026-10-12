import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseValue, framesOf } from "../js/frame_number.mjs";

// Same label format as frame_number_node.make_label.
const H3 = [5, 22, 39, 56, 73, 90, 107, 124, 141, 158, 175, 192]
    .map((f) => `${f}  (${(f / 24).toFixed(2)}s)`);
const LTX = [113, 121, 129].map((f) => `${f}  (${(f / 24).toFixed(2)}s)`);

test("reads the frame count from a label", () => {
    assert.equal(framesOf("362  (15.08s)"), 362);
    assert.equal(framesOf(97), 97);
});

test("keeps a value the preset allows", () => {
    assert.equal(chooseValue(H3, "192  (8.00s)", H3[0]), "192  (8.00s)");
});

test("snaps to the nearest legal count when the preset changes", () => {
    assert.equal(chooseValue(H3, "121  (5.04s)", H3[0]), "124  (5.17s)");
    assert.equal(chooseValue(LTX, "124  (5.17s)", LTX[0]), "121  (5.04s)");
});

test("ties go up, matching the Python node", () => {
    assert.equal(chooseValue(LTX, "125  (5.21s)", LTX[0]), "129  (5.38s)");
});

test("same count with a different fps label maps to this preset's label", () => {
    assert.equal(chooseValue(LTX, "121  (4.84s)", LTX[0]), "121  (5.04s)");
});

test("unreadable values fall back to the preset default", () => {
    assert.equal(chooseValue(LTX, "", "121  (5.04s)"), "121  (5.04s)");
    assert.equal(chooseValue([], "x", "y"), "x");
});
