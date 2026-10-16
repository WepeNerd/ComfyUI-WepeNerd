import assert from "node:assert/strict";
import { test } from "node:test";
import { SECONDS_STEP, chooseValue, framesForSeconds, framesOf, secondsOf } from "../js/frame_number.mjs";

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

test("seconds pick the nearest legal frame count, ties going up", () => {
    assert.equal(framesForSeconds(H3, 24, 8, "124", 5.17), "192");
    assert.equal(framesForSeconds(H3, 24, 5, "192", 8), "124");
    assert.equal(framesForSeconds(LTX, 24, 4.7083, "113", 4.71), "113");
    assert.equal(framesForSeconds(LTX, 24, 5.1667, "113", 4.71), "121");
    assert.equal(framesForSeconds(H3, 24, 100, "124", 5.17), "192");
    assert.equal(framesForSeconds(H3, 24, 0, "124", 5.17), "5");
});

test("a whole step of the seconds arrows always reaches the next count", () => {
    // 124 frames at 24 fps shows 5.17 s. One arrow step is 0.1 s, less than one 17-frame step.
    assert.equal(framesForSeconds(H3, 24, 5.27, "124", 5.17), "141");
    assert.equal(framesForSeconds(H3, 24, 5.07, "124", 5.17), "107");
    assert.equal(framesForSeconds(H3, 24, 0.11, "5", 0.21), "5");
    assert.equal(framesForSeconds(H3, 24, 8.1, "192", 8), "192");
    // A smaller typed change snaps back to the current count.
    assert.equal(framesForSeconds(H3, 24, 5.2, "124", 5.17), "124");
});

test("seconds shown are frames / fps to two decimals", () => {
    assert.equal(secondsOf(124, 24), 5.17);
    assert.equal(secondsOf(81, 16), 5.06);
    assert.equal(SECONDS_STEP, 0.1);
});
