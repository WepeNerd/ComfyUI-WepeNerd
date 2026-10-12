import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { chooseValue } from "./frame_number.mjs?v=1";

const NODE_NAME = "WN_FrameNumberSelector";
const EXTENSION_NAME = "wepenerd.frame_number";
let presetsRequest = null;

function loadPresets() {
    presetsRequest ??= api.fetchApi("/wepenerd/frame_number/presets")
        .then((response) => response.json())
        .then((data) => (data && !data.error ? data : null))
        .catch((error) => {
            console.warn("[WepeNerd] Frame Number Selector: couldn't load presets", error);
            presetsRequest = null;
            return null;
        });
    return presetsRequest;
}

function widget(node, name) {
    return node.widgets?.find((item) => item.name === name) ?? null;
}

function applyPreset(node) {
    const presetWidget = widget(node, "preset");
    const framesWidget = widget(node, "frames");
    if (!presetWidget || !framesWidget) return;
    loadPresets().then((presets) => {
        const preset = presets?.[presetWidget.value];
        if (!preset) return; // keep the full list rather than an empty one
        // A fresh array: options.values can be shared by every node of this type.
        framesWidget.options.values = [...preset.labels];
        const next = chooseValue(preset.labels, framesWidget.value, preset.default);
        if (next !== framesWidget.value) {
            framesWidget.value = next;
            framesWidget.callback?.(next);
        }
        node.setDirtyCanvas?.(true, true);
    });
}

app.registerExtension({
    name: EXTENSION_NAME,
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            const presetWidget = widget(this, "preset");
            if (presetWidget) {
                const node = this;
                const callback = presetWidget.callback;
                presetWidget.callback = function () {
                    const value = callback?.apply(this, arguments);
                    applyPreset(node);
                    return value;
                };
            }
            applyPreset(this);
            return result;
        };

        // Saved workflows restore widget values after creation; filter again
        // without replacing a stored count that is still legal.
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            applyPreset(this);
            return result;
        };
    },
});
