import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import * as helpers from "../js/load_video.mjs";

const INFO = { width: 64, height: 32, fps: 10, duration: 10, frames: 100, frames_exact: true, audio: false, alpha: false };
const PRESETS = { "MiniMax H3 (17n+5, 24 fps)": { fps: 24, rule: [17, 5], min: 5, max: 362 } };

// infoStatuses: HTTP statuses for successive info requests (the last one repeats); 200 returns INFO.
function setup({ infoStatuses = [200] } = {}) {
    const observers = [];
    const requests = [];
    const timers = [];
    let infoCalls = 0;
    let extension;
    const context = vm.createContext({
        ...helpers,
        app: { registerExtension(value) { extension = value; } },
        api: {
            fetchApi(url) {
                requests.push(url);
                if (url.includes("frame_number")) return Promise.resolve({ ok: true, json: () => Promise.resolve(PRESETS) });
                const status = infoStatuses[Math.min(infoCalls++, infoStatuses.length - 1)];
                const body = status === 200 ? INFO : { error: "Video file not found or unsupported: clip.mp4" };
                return Promise.resolve({ ok: status === 200, status, json: () => Promise.resolve(body) });
            },
        },
        MutationObserver: class {
            constructor(callback) { this.callback = callback; observers.push(this); }
            observe() {}
            disconnect() { this.disconnected = true; }
        },
        requestAnimationFrame: () => 1,
        cancelAnimationFrame: () => {},
        setTimeout: (callback, delay) => { if (!delay) callback(); else timers.push(callback); return timers.length; },
        clearTimeout: () => {},
        JSON,
    });
    const source = readFileSync(new URL("../js/wn_load_video.js", import.meta.url), "utf8")
        .replace(/^import [\s\S]*?;\r?\n/gm, "");
    vm.runInContext(source, context);
    const widget = (name, value, extra = {}) => ({ name, value, ...extra });
    class Node {
        constructor() {
            this.widgets = [
                widget("video", "clip.mp4"), widget("start_time", 0), widget("duration", 0),
                widget("frame_load_cap", 121), widget("frame_rate", 0), widget("skip_first_frames", 0),
                widget("select_every_nth", 1),
                widget("format", "None", { options: { values: ["None", ...Object.keys(PRESETS)] } }),
                widget("load_audio", true),
            ];
            this.properties = {};
        }
        onNodeCreated() { this.created = true; }
        addCustomWidget(value) { this.widgets.push(value); return value; }
        addDOMWidget() { return { onRemove() { this.removed = true; } }; }
        onRemoved() { this.removed = true; }
        setDirtyCanvas() {}
    }
    extension.beforeRegisterNodeDef(Node, { name: "WN_LoadVideo" });
    const node = new Node();
    node.onNodeCreated();
    const value = (name) => node.widgets.find((item) => item.name === name);
    return { node, observers, extension, requests, value, timers };
}

function player(reject = false) {
    return {
        isConnected: true, plays: 0, pauses: 0, paused: true, currentTime: 0, duration: 10,
        listeners: {},
        addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); },
        removeEventListener() {},
        emit(type) { for (const handler of this.listeners[type] ?? []) handler(); },
        play() { this.plays++; return reject ? Promise.reject(new Error("blocked")) : Promise.resolve(); },
        pause() { this.pauses++; },
    };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

function draw(node) {
    const info = node.widgets.find((item) => item.name === "clip_info");
    const texts = [];
    const ctx = {
        save() {}, restore() {}, fillText(text) { texts.push(text); }, measureText: (text) => ({ width: text.length }),
    };
    info.draw(ctx, node, 600, 0);
    return texts;
}

test("autoplays newly loaded and replaced previews, preserving manual pause", () => {
    const { node, observers } = setup();
    const container = { players: [], querySelectorAll() { return this.players; } };
    node.addDOMWidget("video-preview", "video", container, {});
    const first = player();
    container.players = [first];
    observers[0].callback();
    assert.ok(node.created);
    for (const flag of ["autoplay", "muted", "defaultMuted", "playsInline", "controls"]) {
        assert.equal(first[flag], true);
    }
    first.pause();
    observers[0].callback();
    assert.equal(first.plays, 1);
    first.isConnected = false;
    const second = player();
    container.players = [second];
    observers[0].callback();
    assert.equal(first.pauses, 2);
    assert.equal(second.plays, 1);
    node.onRemoved();
    assert.equal(second.pauses, 1);
    assert.ok(observers[0].disconnected);
    assert.ok(node.removed);
});

test("enhances restored previews and cleans up when the widget is removed", () => {
    const { node, observers } = setup();
    const media = player();
    const container = { querySelectorAll: () => [media] };
    const widget = node.addDOMWidget("video-preview", "video", container, {});
    assert.equal(media.plays, 1);
    widget.onRemove();
    assert.equal(media.pauses, 1);
    assert.ok(observers[0].disconnected);
    assert.ok(widget.removed);
});

test("autoplay rejection leaves controls available without an unhandled promise", async () => {
    const { node } = setup();
    const media = player(true);
    node.addDOMWidget("video-preview", "video", { querySelectorAll: () => [media] }, {});
    await Promise.resolve();
    assert.equal(media.controls, true);
});

test("shows what will load and loops the preview over the capped clip", async () => {
    const { node, value, requests } = setup();
    const media = player();
    node.addDOMWidget("video-preview", "video", { querySelectorAll: () => [media] }, {});
    draw(node);
    await flush();
    const lines = draw(node);
    assert.ok(requests.includes("/wepenerd/load_video/info?video=clip.mp4"));
    assert.match(lines[0], /64×32 · 10 fps · 10 s · 100 frames/);
    assert.match(lines[1], /100 frames · 10 fps · 10 s · from 0 s/);
    assert.equal(media.loop, false);
    value("frame_load_cap").value = 30;
    value("start_time").value = 2;
    media.currentTime = 7;
    assert.match(draw(node)[1], /30 frames · 10 fps · 3 s · from 2 s/);
    assert.equal(media.currentTime, 2);
});

test("a dropped video still uploading is retried instead of shown as missing", async () => {
    const { node, requests, timers } = setup({ infoStatuses: [404, 404, 200] });
    draw(node);
    await flush();
    assert.deepEqual(draw(node), ["Reading video…"]);
    timers.shift()();
    await flush();
    assert.deepEqual(draw(node), ["Reading video…"]);
    timers.shift()();
    await flush();
    assert.match(draw(node)[1], /100 frames/);
    assert.equal(requests.filter((url) => url.includes("load_video/info")).length, 3);
});

test("a file that stays missing shows the error after the retries", async () => {
    const { node, timers } = setup({ infoStatuses: [404] });
    draw(node);
    await flush();
    while (timers.length) { timers.shift()(); await flush(); }
    assert.match(draw(node).join(" "), /Video file not found/);
});

test("the preview finishing loading reads the file again", async () => {
    const { node, requests } = setup({ infoStatuses: [400, 200] });
    const media = player();
    node.addDOMWidget("video-preview", "video", { querySelectorAll: () => [media] }, {});
    draw(node);
    await flush();
    assert.match(draw(node).join(" "), /not found/);
    media.emit("loadeddata");
    await flush();
    assert.match(draw(node)[1], /100 frames/);
    assert.equal(requests.filter((url) => url.includes("load_video/info")).length, 2);
});

test("picking a format sets its frame rate; loading a saved workflow does not", async () => {
    const { node, value } = setup();
    await flush();
    value("format").value = "MiniMax H3 (17n+5, 24 fps)";
    value("format").callback("MiniMax H3 (17n+5, 24 fps)");
    assert.equal(value("frame_rate").value, 24);
    value("frame_rate").value = 0;
    node.onConfigure({ properties: { wn_load_video_v2: true }, widgets_values: [] });
    assert.equal(value("frame_rate").value, 0);
});

test("workflows saved before the update keep their settings", () => {
    const { node, value } = setup();
    node.onConfigure({ properties: {}, widgets_values: ["clip.mp4", 12, 0, 3, 2, "H3", 768, 512, false] });
    assert.deepEqual(
        ["video", "start_time", "duration", "frame_load_cap", "frame_rate", "skip_first_frames",
            "select_every_nth", "format", "load_audio"].map((name) => value(name).value),
        ["clip.mp4", 0, 0, 0, 12, 3, 2, "MiniMax H3 (17n+5, 24 fps)", false]);
    assert.equal(node.properties.wn_load_video_v2, true);
});

test("does not modify unrelated nodes or widgets", () => {
    const { node, observers, extension } = setup();
    node.addDOMWidget("image-preview", "image", {}, {});
    assert.equal(observers.length, 0);
    class OtherNode {}
    extension.beforeRegisterNodeDef(OtherNode, { name: "LoadVideo" });
    assert.equal(OtherNode.prototype.onNodeCreated, undefined);
});
