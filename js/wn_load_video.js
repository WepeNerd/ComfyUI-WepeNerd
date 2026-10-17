import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    ClipLooper, VALUES_MARKER, WATCHED, describe, linkedValue, migrateLegacyValues, planClip, resolveFormatName,
} from "./load_video.mjs";

const LINE = 15;
let presetsRequest = null;

function loadPresets() {
    presetsRequest ??= api.fetchApi("/wepenerd/frame_number/presets")
        .then((response) => (response.ok ? response.json() : {}))
        .catch(() => ({}));
    return presetsRequest;
}

const widgetOf = (node, name) => node.widgets?.find((widget) => widget.name === name);

// Current settings. A connected input uses the value of the node it is connected to when the browser
// can read it (Duration Selector, primitives), so the readout and preview follow that node live.
function settingsOf(node, presets) {
    const settings = { unresolved: [] };
    for (const name of WATCHED) {
        const linked = linkedValue(node.graph ?? app.graph, node, name, presets);
        if (linked.linked && linked.unknown) settings.unresolved.push(name);
        settings[name] = linked.linked && !linked.unknown ? linked.value : widgetOf(node, name)?.value;
    }
    for (const name of WATCHED.slice(1, -1)) settings[name] = Number(settings[name]) || 0;
    settings.select_every_nth = Math.max(1, settings.select_every_nth);
    return settings;
}

// Autoplay the source preview muted, and loop it over the clip the current settings load.
function enhancePreview(node, looper) {
    const observers = new Map();
    const players = new Set();
    function watch(container) {
        if (!container || observers.has(container)) return;
        const update = () => {
            for (const player of players) {
                if (!player.isConnected) {
                    player.pause();
                    looper.detach(player);
                    players.delete(player);
                }
            }
            for (const player of container.querySelectorAll("video")) {
                if (players.has(player)) continue;
                players.add(player);
                player.autoplay = true;
                player.defaultMuted = true;
                player.muted = true;
                player.loop = true;
                player.playsInline = true;
                player.controls = true;
                looper.attach(player);
                player.play().catch(() => { /* Playback controls remain available if autoplay is blocked. */ });
            }
        };
        const observer = new MutationObserver(update);
        observer.observe(container, { childList: true, subtree: true });
        observers.set(container, observer);
        update();
    }
    const addDOMWidget = node.addDOMWidget;
    node.addDOMWidget = function (name, type, element, options) {
        const widget = addDOMWidget.apply(this, arguments);
        if (type === "video") {
            watch(element);
            const onRemove = widget.onRemove;
            widget.onRemove = function () {
                observers.get(element)?.disconnect();
                observers.delete(element);
                for (const player of element.querySelectorAll("video")) {
                    player.pause();
                    looper.detach(player);
                    players.delete(player);
                }
                return onRemove?.apply(this, arguments);
            };
        }
        return widget;
    };
    watch(node.videoContainer);
    const onRemoved = node.onRemoved;
    node.onRemoved = function () {
        for (const observer of observers.values()) observer.disconnect();
        for (const player of players) player.pause();
        looper.clear();
        observers.clear();
        players.clear();
        return onRemoved?.apply(this, arguments);
    };
}

function installInfo(node) {
    const looper = new ClipLooper();
    const state = { key: null, video: null, info: null, presets: {}, lines: null };
    loadPresets().then((presets) => {
        state.presets = presets;
        state.key = null;
        node.setDirtyCanvas?.(true, false);
    });

    const presetFor = (name) => {
        const preset = state.presets[name];
        return preset ? { rule: preset.rule, frames: preset.frames, min: preset.min, max: preset.max } : null;
    };

    function fetchInfo(video) {
        state.video = video;
        state.info = null;
        if (!video) return;
        api.fetchApi(`/wepenerd/load_video/info?video=${encodeURIComponent(video)}`)
            .then(async (response) => {
                const data = await response.json().catch(() => ({}));
                return response.ok ? data : { error: data.error || "Couldn't read this video." };
            })
            .catch(() => ({ error: "Couldn't reach ComfyUI to read this video." }))
            .then((info) => {
                if (state.video !== video) return;
                state.info = info;
                state.key = null;
                node.setDirtyCanvas?.(true, false);
            });
    }

    // Recompute whenever a watched value changes, however it changed (typing, loading a workflow,
    // an upload, or a change on a connected node), by comparing values each time the widget is drawn.
    function refresh() {
        const settings = settingsOf(node, state.presets);
        const key = JSON.stringify(settings);
        if (key === state.key) return;
        state.key = key;
        if (settings.video !== state.video) fetchInfo(settings.video);
        const preset = presetFor(settings.format);
        state.lines = describe(state.info, settings, preset, settings.format);
        if (state.info && !state.info.error && !settings.unresolved.length) {
            const plan = planClip(state.info, settings, preset);
            looper.setRange(plan.clip_start, plan.count ? plan.clip_end : NaN);
        } else {
            looper.setRange(NaN, NaN);
        }
    }

    const widget = {
        name: "clip_info", type: "wn_video_info", value: "", options: { serialize: false },
        serialize: false,
        computeSize: (width) => [width ?? 300, LINE * 3 + 6],
        draw(ctx, _node, width, y) {
            refresh();
            const lines = state.lines;
            ctx.save();
            ctx.font = "11px Arial, sans-serif";
            ctx.textBaseline = "middle";
            ctx.textAlign = "left";
            const fit = (text) => {
                const room = width - 30;
                if (ctx.measureText(text).width <= room) return text;
                while (text.length > 1 && ctx.measureText(`${text}…`).width > room) text = text.slice(0, -1);
                return `${text}…`;
            };
            const rows = !lines ? [["", "#8a8a8a"]] : !state.info && state.video ? [["Reading video…", "#8a8a8a"]] : [
                [lines.source, "#8a8a8a"],
                [lines.output, "#cfcfcf"],
                [lines.note, lines.warn ? "#e0a050" : "#8a8a8a"],
            ];
            rows.forEach(([text, color], index) => {
                if (!text) return;
                ctx.fillStyle = color;
                ctx.fillText(fit(text), 15, y + 3 + LINE * index + LINE / 2);
            });
            ctx.restore();
        },
        mouse: () => false,
    };
    node.addCustomWidget ? node.addCustomWidget(widget) : node.widgets?.push(widget);

    // Picking a model preset also sets its native frame rate (only when you pick it, not on load).
    const format = widgetOf(node, "format");
    if (format) {
        const callback = format.callback;
        format.callback = function (value) {
            const result = callback?.apply(this, arguments);
            const fps = state.presets[value]?.fps;
            const rate = widgetOf(node, "frame_rate");
            if (fps && rate) {
                rate.value = fps;
                rate.callback?.(fps);
            }
            node.setDirtyCanvas?.(true, true);
            return result;
        };
    }
    enhancePreview(node, looper);
    return { refresh: () => { state.key = null; } };
}

function migrate(node, info) {
    if (!info || info.properties?.[VALUES_MARKER]) return;
    const values = migrateLegacyValues(info.widgets_values);
    if (!values) return;
    for (const [name, value] of Object.entries(values)) {
        const widget = widgetOf(node, name);
        if (widget) widget.value = value;
    }
}

function fixFormat(node) {
    const format = widgetOf(node, "format");
    const names = format?.options?.values;
    if (Array.isArray(names)) format.value = resolveFormatName(format.value, names);
}

app.registerExtension({
    name: "WepeNerd.LoadVideo",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "WN_LoadVideo") return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            this.properties ??= {};
            this.properties[VALUES_MARKER] = true;
            this.wnLoadVideo = installInfo(this);
            return result;
        };
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const result = onConfigure?.apply(this, arguments);
            const apply = () => {
                migrate(this, info);
                fixFormat(this);
                this.properties ??= {};
                this.properties[VALUES_MARKER] = true;
                this.wnLoadVideo?.refresh();
                this.setDirtyCanvas?.(true, true);
            };
            apply();
            // Widget values may be applied after onConfigure in some frontend versions.
            setTimeout(apply, 0);
            return result;
        };
    },
});
