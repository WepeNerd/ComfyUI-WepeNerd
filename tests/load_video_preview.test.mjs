import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function setup() {
    const observers = [];
    let extension;
    const context = vm.createContext({
        app: { registerExtension(value) { extension = value; } },
        MutationObserver: class {
            constructor(callback) { this.callback = callback; observers.push(this); }
            observe() {}
            disconnect() { this.disconnected = true; }
        },
    });
    const source = readFileSync(new URL("../js/wn_load_video.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "");
    vm.runInContext(source, context);
    class Node {
        onNodeCreated() { this.created = true; }
        addDOMWidget() { return { onRemove() { this.removed = true; } }; }
        onRemoved() { this.removed = true; }
    }
    extension.beforeRegisterNodeDef(Node, { name: "WN_LoadVideo" });
    const node = new Node();
    node.onNodeCreated();
    return { node, observers, extension };
}

function player(reject = false) {
    return {
        isConnected: true, plays: 0, pauses: 0,
        play() { this.plays++; return reject ? Promise.reject(new Error("blocked")) : Promise.resolve(); },
        pause() { this.pauses++; },
    };
}

test("autoplays newly loaded and replaced previews, preserving manual pause", () => {
    const { node, observers } = setup();
    const container = { players: [], querySelectorAll() { return this.players; } };
    node.addDOMWidget("video-preview", "video", container, {});
    const first = player();
    container.players = [first];
    observers[0].callback();
    assert.ok(node.created);
    for (const flag of ["autoplay", "muted", "defaultMuted", "loop", "playsInline", "controls"]) {
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

test("does not modify unrelated nodes or widgets", () => {
    const { node, observers, extension } = setup();
    node.addDOMWidget("image-preview", "image", {}, {});
    assert.equal(observers.length, 0);
    class OtherNode {}
    extension.beforeRegisterNodeDef(OtherNode, { name: "LoadVideo" });
    assert.equal(OtherNode.prototype.onNodeCreated, undefined);
});
