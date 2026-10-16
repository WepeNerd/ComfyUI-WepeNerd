import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseValue, framesOf } from "../js/frame_number.mjs";

// Same plain-number labels as frame_number_node.make_label.
const H3 = ["5", "22", "39", "56", "73", "90", "107", "124", "141", "158", "175", "192"];
const LTX = ["113", "121", "129"];

test("reads the frame count from a label", () => {
    assert.equal(framesOf("362"), 362);
    assert.equal(framesOf(97), 97);
});

test("keeps a value the preset allows", () => {
    assert.equal(chooseValue(H3, "192", H3[0]), "192");
});

test("snaps to the nearest valid count when the preset changes", () => {
    assert.equal(chooseValue(H3, "121", H3[0]), "124");
    assert.equal(chooseValue(LTX, "124", LTX[0]), "121");
});

test("ties go up, matching the Python node", () => {
    assert.equal(chooseValue(LTX, "125", LTX[0]), "129");
});

test("older labels with seconds map onto the plain number", () => {
    assert.equal(chooseValue(LTX, "121  (5.04s)", LTX[0]), "121");
    assert.equal(chooseValue(H3, "121  (5.04s)", H3[0]), "124");
});

test("unreadable values fall back to the preset default", () => {
    assert.equal(chooseValue(LTX, "", "121"), "121");
    assert.equal(chooseValue([], "x", "y"), "x");
});
