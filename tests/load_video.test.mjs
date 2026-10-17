import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
    ClipLooper, LEGACY_FORMATS, describe, formatSeconds, largestValid, linkedValue, migrateLegacyValues,
    migrateV2Values, planClip, windowToFrames,
    resolveFormatName,
} from "../js/load_video.mjs";

const shared = JSON.parse(readFileSync(new URL("./load_video_plans.json", import.meta.url), "utf8"));

test("planClip matches the Python planner on every shared case", () => {
    for (const { info, settings, preset, expected } of shared.cases) {
        const plan = planClip(shared.infos[info], settings, preset ? shared.presets[preset] : null);
        const label = JSON.stringify({ info, settings, preset });
        assert.equal(plan.count, expected.count, label);
        assert.equal(plan.estimate, expected.estimate, label);
        for (const key of ["frame_rate", "seconds", "clip_start", "clip_end"]) {
            assert.ok(Math.abs(plan[key] - expected[key]) < 1e-6, `${label} ${key}`);
        }
    }
});

test("largestValid follows rules and explicit lists", () => {
    const h3 = { rule: [17, 5] };
    assert.equal(largestValid(121, h3), 107);
    assert.equal(largestValid(4, h3), 0);
    assert.equal(largestValid(9, { rule: [8, 1] }), 9);
    assert.equal(largestValid(7, { rule: [8, 0] }), 0);
    assert.equal(largestValid(60, { rule: null, frames: [25, 49, 97] }), 49);
    assert.equal(largestValid(60, null), 60);
});

test("old saved widget values are read by position and old format names are mapped", () => {
    const old = ["clip.mp4", 24, 0, 2, 1, "LTXV", 768, 512, false];
    assert.deepEqual(migrateLegacyValues(old), {
        video: "clip.mp4", frame_load_cap: 0, frame_rate: 24,
        skip_first_frames: 2, select_every_nth: 1, format: "LTXV", load_audio: false,
    });
    // An extra serialized widget (such as an upload button) ahead of the values is tolerated.
    assert.equal(migrateLegacyValues(["image", ...old]).video, "clip.mp4");
    assert.equal(migrateLegacyValues(["clip.mp4", 0, 0, 121, 0, 0, 1, "None", true]), null);
    const names = ["None", ...Object.values(LEGACY_FORMATS).filter((name) => name !== "None")];
    assert.equal(resolveFormatName("LTXV", names), "LTX-Video 0.9.x (8n+1, 25 fps)");
    assert.equal(resolveFormatName("AnimateDiff", names), "None");
    assert.equal(resolveFormatName("Wan 2.2 5B (4n+1, 24 fps)", names), "Wan 2.2 5B (4n+1, 24 fps)");
});

test("workflows saved with start_time and duration turn them into skip and cap", () => {
    const saved = ["clip.mp4", 2, 3, 121, 0, 6, 2, "None", true, "image"];
    const values = migrateV2Values(saved);
    assert.deepEqual(values, { video: "clip.mp4", start_time: 2, duration: 3, frame_load_cap: 121, frame_rate: 0,
        skip_first_frames: 6, select_every_nth: 2, format: "None", load_audio: true });
    // At 30 fps: start 2 s = 60 frames skipped; 3 s = 90 frames, minus the 6 skipped, every 2nd = 42.
    assert.deepEqual(windowToFrames(values, 30), { skip_first_frames: 66, frame_load_cap: 42 });
    assert.deepEqual(windowToFrames({ ...values, duration: 0 }, 30), { skip_first_frames: 66, frame_load_cap: 121 });
    assert.deepEqual(windowToFrames({ ...values, frame_load_cap: 0, duration: 1 }, 24), { skip_first_frames: 54, frame_load_cap: 9 });
    assert.equal(migrateV2Values(["clip.mp4", 24, 0, 2, 1, "LTXV", 768, 512, false]), null);
});

test("describe summarises the source, the clip and problems", () => {
    const info = { width: 1920, height: 1080, fps: 30, duration: 60, frames: 1800, frames_exact: true, audio: true };
    let lines = describe(info, { frame_load_cap: 121 }, null);
    assert.match(lines.source, /1920×1080 · 30 fps · 1m 00s · 1800 frames · audio/);
    assert.match(lines.output, /^Loads {2}121 frames · 30 fps · 4\.03 s · from 0 s · 2\.8 GB$/);
    assert.equal(lines.note, "");
    lines = describe(info, { skip_first_frames: 1800 }, null);
    assert.equal(lines.output, "Loads nothing");
    assert.match(lines.note, /past the end/);
    lines = describe(info, { frame_load_cap: 4 }, { rule: [17, 5], min: 5, max: 362 }, "H3");
    assert.match(lines.note, /Too few frames for H3/);
    lines = describe(info, { frame_load_cap: 9 }, { rule: [8, 1], min: 9, max: 481 });
    assert.equal(lines.note, "");
    lines = describe(info, {}, null);
    assert.match(lines.note, /^No cap: the whole video needs 41\.7 GB of RAM\.$/);
    assert.equal(describe({ error: "Video file not found" }, {}, null).note, "Video file not found");
    assert.equal(formatSeconds(3725), "1h 02m");
});

function fakePlayer({ duration = 10, paused = true } = {}) {
    const listeners = {};
    return {
        currentTime: 0, duration, paused, loop: true, plays: 0,
        addEventListener(type, handler) { listeners[type] = handler; },
        removeEventListener(type) { delete listeners[type]; },
        emit(type) { listeners[type]?.(); },
        listeners,
        play() { this.plays++; this.paused = false; this.emit("play"); return Promise.resolve(); },
        pause() { this.paused = true; this.emit("pause"); },
    };
}

function scheduler() {
    const queue = new Map();
    let next = 1;
    return {
        schedule: (callback) => { queue.set(next, callback); return next++; },
        cancel: (id) => queue.delete(id),
        flush() { const pending = [...queue.values()]; queue.clear(); for (const callback of pending) callback(); },
        get size() { return queue.size; },
    };
}

test("the preview loops over the selected clip and restarts it when the range changes", () => {
    const clock = scheduler();
    const looper = new ClipLooper(clock.schedule, clock.cancel);
    const player = fakePlayer();
    looper.attach(player);
    looper.setRange(2, 6);
    assert.equal(player.loop, false);
    assert.equal(player.currentTime, 2);
    player.play();
    player.currentTime = 5.9;
    clock.flush();
    assert.equal(player.currentTime, 5.9);
    player.currentTime = 6.01;
    clock.flush();
    assert.equal(player.currentTime, 2);
    // A shorter cap moves the preview back to the start of the new clip.
    player.currentTime = 3.5;
    looper.setRange(2, 3);
    assert.equal(player.currentTime, 2);
    // The same range again (any redraw) leaves playback alone.
    player.currentTime = 2.5;
    looper.setRange(2, 3);
    assert.equal(player.currentTime, 2.5);
});

test("a clip that runs to the end of the file restarts on 'ended'; paused players can be scrubbed", () => {
    const clock = scheduler();
    const looper = new ClipLooper(clock.schedule, clock.cancel);
    const player = fakePlayer({ duration: 8 });
    looper.attach(player);
    looper.setRange(5, 8);
    player.play();
    player.currentTime = 7.999;
    clock.flush();
    assert.equal(player.currentTime, 7.999);
    player.paused = true;
    player.emit("ended");
    assert.equal(player.currentTime, 5);
    assert.equal(player.paused, false);
    player.pause();
    assert.equal(clock.size, 0);
    player.currentTime = 1;
    clock.flush();
    assert.equal(player.currentTime, 1);
    player.play();
    assert.equal(player.currentTime, 5);
});

test("without a range the whole file loops, and detaching removes listeners", () => {
    const clock = scheduler();
    const looper = new ClipLooper(clock.schedule, clock.cancel);
    const player = fakePlayer();
    looper.attach(player);
    looper.setRange(1, 2);
    looper.setRange(NaN, NaN);
    assert.equal(player.loop, true);
    player.play();
    player.currentTime = 9;
    clock.flush();
    assert.equal(player.currentTime, 9);
    looper.detach(player);
    assert.deepEqual(Object.keys(player.listeners), []);
    assert.equal(clock.size, 0);
});

function fakeGraph(nodes, links) {
    return {
        links: new Map(Object.entries(links).map(([id, link]) => [Number(id), link])),
        getNodeById: (id) => nodes[id],
    };
}

test("connected inputs read the Duration Selector, primitives and reroutes", () => {
    const presets = { H3: { fps: 24 } };
    const duration = {
        type: "WN_FrameNumberSelector",
        widgets: [{ name: "preset", value: "H3" }, { name: "frames", value: "124" }, { name: "seconds", value: 5.17 }],
        outputs: [{ name: "frames" }, { name: "fps" }, { name: "seconds" }],
    };
    const primitive = { type: "PrimitiveNode", outputs: [{ name: "FLOAT" }],
        widgets: [{ name: "value", value: 2.5 }, { name: "control_after_generate", value: "fixed" }] };
    const reroute = { type: "Reroute", inputs: [{ name: "", link: 5 }], outputs: [{ name: "" }] };
    const math = { type: "SomeMath", outputs: [{ name: "INT" }], widgets: [{ name: "a", value: 3 }, { name: "b", value: 4 }] };
    const video = { inputs: [
        { name: "frame_load_cap", widget: { name: "frame_load_cap" }, link: 1 },
        { name: "frame_rate", widget: { name: "frame_rate" }, link: 2 },
        { name: "seconds_in", widget: { name: "seconds_in" }, link: 3 },
        { name: "primitive_in", widget: { name: "primitive_in" }, link: 4 },
        { name: "skip_first_frames", widget: { name: "skip_first_frames" }, link: 6 },
        { name: "select_every_nth", widget: { name: "select_every_nth" }, link: null },
    ] };
    const graph = fakeGraph({ 1: duration, 2: primitive, 3: reroute, 4: math }, {
        1: { origin_id: 1, origin_slot: 0 }, 2: { origin_id: 1, origin_slot: 1 }, 3: { origin_id: 3, origin_slot: 0 },
        4: { origin_id: 2, origin_slot: 0 }, 5: { origin_id: 1, origin_slot: 2 }, 6: { origin_id: 4, origin_slot: 0 },
    });
    assert.deepEqual(linkedValue(graph, video, "frame_load_cap", presets), { linked: true, value: 124 });
    assert.deepEqual(linkedValue(graph, video, "frame_rate", presets), { linked: true, value: 24 });
    assert.ok(Math.abs(linkedValue(graph, video, "seconds_in", presets).value - 124 / 24) < 1e-9);
    assert.deepEqual(linkedValue(graph, video, "primitive_in", presets), { linked: true, value: 2.5 });
    assert.deepEqual(linkedValue(graph, video, "skip_first_frames", presets), { linked: true, unknown: true });
    assert.deepEqual(linkedValue(graph, video, "select_every_nth", presets), { linked: false });
    // Without presets the fps of a Duration Selector is unknown, but its frames are not.
    assert.deepEqual(linkedValue(graph, video, "frame_rate", {}), { linked: true, unknown: true });
    assert.deepEqual(linkedValue(graph, video, "frame_load_cap", {}), { linked: true, value: 124 });
});

test("a value known only at run time falls back to the setting, marked approximate", () => {
    const info = { width: 64, height: 32, fps: 10, duration: 10, frames: 100, frames_exact: true };
    const lines = describe(info, { frame_load_cap: 30, skip_first_frames: 5, approximate: ["skip_first_frames"] }, null);
    assert.match(lines.output, /^Loads {2}≈30 frames · 10 fps · 3 s · from 0\.5 s/);
    assert.match(lines.note, /skip_first_frames is only known when the workflow runs; using skip_first_frames 5/);
});

test("Number Convert passes values through, truncating for INT", () => {
    const constant = { type: "INTConstant", outputs: [{ name: "value" }], inputs: [{ name: "value", widget: { name: "value" }, link: null }],
        widgets: [{ name: "value", value: 24 }] };
    const floatConst = { type: "PrimitiveFloat", outputs: [{ name: "FLOAT" }], widgets: [{ name: "value", value: 29.97 }] };
    const convert = (link) => ({ type: "ComfyNumberConvert", inputs: [{ name: "value", link }],
        outputs: [{ name: "FLOAT", type: "FLOAT" }, { name: "INT", type: "INT" }], widgets: [] });
    const nodes = { 1: constant, 2: convert(1), 3: floatConst, 4: convert(2), 5: convert(null) };
    const graph = fakeGraph(nodes, {
        1: { origin_id: 1, origin_slot: 0 }, 2: { origin_id: 3, origin_slot: 0 },
        10: { origin_id: 2, origin_slot: 0 }, 11: { origin_id: 4, origin_slot: 1 }, 12: { origin_id: 5, origin_slot: 0 },
    });
    const video = { inputs: [
        { name: "frame_rate", widget: { name: "frame_rate" }, link: 10 },
        { name: "skip_first_frames", widget: { name: "skip_first_frames" }, link: 11 },
        { name: "frame_load_cap", widget: { name: "frame_load_cap" }, link: 12 },
    ] };
    assert.deepEqual(linkedValue(graph, video, "frame_rate"), { linked: true, value: 24 });
    assert.deepEqual(linkedValue(graph, video, "skip_first_frames"), { linked: true, value: 29 });
    assert.deepEqual(linkedValue(graph, video, "frame_load_cap"), { linked: true, unknown: true });
});

test("connected inputs follow KJNodes Get/Set pairs", () => {
    const duration = {
        type: "WN_FrameNumberSelector",
        widgets: [{ name: "preset", value: "H3" }, { name: "frames", value: "107" }],
        outputs: [{ name: "frames" }, { name: "fps" }, { name: "seconds" }],
    };
    const set = { type: "SetNode", widgets: [{ name: "Constant", value: "frames" }], inputs: [{ name: "INT", link: 1 }] };
    const get = { type: "GetNode", widgets: [{ name: "Constant", value: "frames" }], outputs: [{ name: "INT" }] };
    const lonely = { type: "GetNode", widgets: [{ name: "Constant", value: "missing" }], outputs: [{ name: "INT" }] };
    const video = { inputs: [
        { name: "frame_load_cap", widget: { name: "frame_load_cap" }, link: 2 },
        { name: "skip_first_frames", widget: { name: "skip_first_frames" }, link: 3 },
    ] };
    const nodes = { 1: duration, 2: set, 3: get, 4: lonely };
    const graph = {
        _nodes: Object.values(nodes),
        links: { 1: { origin_id: 1, origin_slot: 0 }, 2: { origin_id: 3, origin_slot: 0 }, 3: { origin_id: 4, origin_slot: 0 } },
        getNodeById: (id) => nodes[id],
    };
    assert.deepEqual(linkedValue(graph, video, "frame_load_cap", {}), { linked: true, value: 107 });
    assert.deepEqual(linkedValue(graph, video, "skip_first_frames", {}), { linked: true, unknown: true });
});

test("primitives are read by their value widget whatever their control widget is called", () => {
    const int = { type: "PrimitiveInt", outputs: [{ name: "INT", type: "INT" }],
        inputs: [{ name: "value", widget: { name: "value" }, link: null }],
        widgets: [{ name: "value", type: "number", value: 24 }, { name: "fixed", type: "combo", value: "fixed" }] };
    const virtual = { type: "PrimitiveNode", outputs: [{ name: "connect to widget input" }],
        widgets: [{ name: "frame_rate", type: "number", value: 16 }, { name: "control_after_generate", type: "combo", value: "fixed" }] };
    const graph = fakeGraph({ 1: int, 2: virtual }, { 1: { origin_id: 1, origin_slot: 0 }, 2: { origin_id: 2, origin_slot: 0 } });
    const video = { inputs: [{ name: "frame_load_cap", widget: { name: "frame_load_cap" }, link: 1 },
        { name: "frame_rate", widget: { name: "frame_rate" }, link: 2 }] };
    assert.deepEqual(linkedValue(graph, video, "frame_load_cap"), { linked: true, value: 24 });
    assert.deepEqual(linkedValue(graph, video, "frame_rate"), { linked: true, value: 16 });
});
