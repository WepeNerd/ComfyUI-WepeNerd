import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
    ClipLooper, LEGACY_FORMATS, describe, formatSeconds, largestValid, migrateLegacyValues, planClip,
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
        video: "clip.mp4", start_time: 0, duration: 0, frame_load_cap: 0, frame_rate: 24,
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

test("describe summarises the source, the clip and problems", () => {
    const info = { width: 1920, height: 1080, fps: 30, duration: 60, frames: 1800, frames_exact: true, audio: true };
    let lines = describe(info, { frame_load_cap: 121 }, null);
    assert.match(lines.source, /1920×1080 · 30 fps · 1m 00s · 1800 frames · audio/);
    assert.match(lines.output, /^Loads {2}121 frames · 30 fps · 4\.03 s · from 0 s · 2\.8 GB$/);
    assert.equal(lines.note, "");
    lines = describe(info, { start_time: 70 }, null);
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
