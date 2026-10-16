import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    SECONDS_STEP, chooseValue, framesForSeconds, framesOf, secondsOf,
} from "./frame_number.mjs?v=2";

// Shown as "Duration Selector"; the node id stays WN_FrameNumberSelector so saved workflows load.
const NODE_NAME = "WN_FrameNumberSelector";
const EXTENSION_NAME = "wepenerd.frame_number";
let presetsRequest = null;

function loadPresets() {
    presetsRequest ??= api.fetchApi("/wepenerd/frame_number/presets")
        .then((response) => response.json())
        .then((data) => (data && !data.error ? data : null))
        .catch((error) => {
            console.warn("[WepeNerd] Duration Selector: couldn't load presets", error);
            presetsRequest = null;
            return null;
        });
    return presetsRequest;
}

function widget(node, name) {
    return node.widgets?.find((item) => item.name === name) ?? null;
}

function setFrames(node, label) {
    const framesWidget = widget(node, "frames");
    if (!framesWidget || label === framesWidget.value) return;
    framesWidget.value = label;
    framesWidget.callback?.(label);
}

// Show the selected count's length in the seconds field.
function showSeconds(node, preset) {
    const secondsWidget = widget(node, "seconds");
    const frames = framesOf(widget(node, "frames")?.value);
    if (!secondsWidget || !preset || !Number.isFinite(frames)) return;
    secondsWidget.value = secondsOf(frames, preset.fps);
    node.setDirtyCanvas?.(true, true);
}

// Filter the frames list to the preset. keepDuration (a preset picked by hand) keeps the clip
// length, so 5 s stays about 5 s at the new model's frame rate; otherwise (creating or loading a
// node) a stored count the preset allows is kept as it is.
function applyPreset(node, keepDuration = false) {
    const presetWidget = widget(node, "preset");
    const framesWidget = widget(node, "frames");
    if (!presetWidget || !framesWidget) return Promise.resolve();
    return loadPresets().then((presets) => {
        const preset = presets?.[presetWidget.value];
        if (!preset) return; // keep the full list rather than an empty one
        const previous = presets[node.wnPreset];
        // A fresh array: options.values can be shared by every node of this type.
        framesWidget.options.values = [...preset.labels];
        const frames = framesOf(framesWidget.value);
        const next = keepDuration && previous && Number.isFinite(frames)
            ? framesForSeconds(preset.labels, preset.fps, frames / previous.fps, null, frames / previous.fps)
            : chooseValue(preset.labels, framesWidget.value, preset.default);
        node.wnPreset = presetWidget.value;
        setFrames(node, next);
        showSeconds(node, preset);
    });
}

function installSeconds(node) {
    const presetWidget = widget(node, "preset");
    const framesWidget = widget(node, "frames");
    if (!presetWidget || !framesWidget || widget(node, "seconds")) return;

    const preset = () => presetsRequest?.then((presets) => presets?.[presetWidget.value]);
    const secondsWidget = node.addWidget("number", "seconds", 0, (value) => {
        loadPresets().then((presets) => {
            const current = presets?.[presetWidget.value];
            if (!current) return;
            const shown = secondsOf(framesOf(framesWidget.value), current.fps);
            setFrames(node, framesForSeconds(current.labels, current.fps, Number(value), framesWidget.value, shown));
            showSeconds(node, current);
        });
    }, { min: 0, max: 3600, step: SECONDS_STEP * 10, step2: SECONDS_STEP, precision: 2, round: 0.01 });
    // Derived from frames, so it is not saved or sent with the prompt.
    secondsWidget.serialize = false;
    secondsWidget.options.serialize = false;
    secondsWidget.tooltip = "Clip length in seconds. Editing it picks the nearest legal frame count; "
        + "the arrows step to the next or previous one.";

    const framesCallback = framesWidget.callback;
    framesWidget.callback = function () {
        const result = framesCallback?.apply(this, arguments);
        preset()?.then((current) => showSeconds(node, current));
        return result;
    };
    const presetCallback = presetWidget.callback;
    presetWidget.callback = function () {
        const result = presetCallback?.apply(this, arguments);
        applyPreset(node, true);
        return result;
    };
    node.setSize?.(node.computeSize?.() ?? node.size);
}

app.registerExtension({
    name: EXTENSION_NAME,
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            installSeconds(this);
            applyPreset(this);
            return result;
        };

        // Saved workflows restore widget values after creation; filter again
        // without replacing a stored count that is still legal.
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            this.wnPreset = undefined;
            applyPreset(this);
            return result;
        };
    },
});
