import { app } from "../../scripts/app.js";

const NODE_NAME = "WN_Slider";
const EXTENSION_NAME = "wepenerd.slider";
const CALIBRATION_WIDGETS = [
    "label",
    "low_value",
    "center_value",
    "high_value",
    "curve",
];

const WIDGET_LABELS = Object.freeze({
    label: "Label",
    low_value: "LOW (-1)",
    center_value: "CENTER (0)",
    high_value: "HIGH (+1)",
    curve: "Curve",
});

const MARGIN = 15;
const SIMPLE_HEIGHT = 58;
const CALIBRATION_HEIGHT = 76;

function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
}

function widget(node, name) {
    return node.widgets?.find((item) => item.name === name) ?? null;
}

function valueOf(node, name, fallback) {
    const item = widget(node, name);
    return item?.value ?? fallback;
}

function calibration(node) {
    return [
        Number(valueOf(node, "low_value", -1)),
        Number(valueOf(node, "center_value", 0)),
        Number(valueOf(node, "high_value", 1)),
    ];
}

function mappedValue(node, normalized) {
    const [low, center, high] = calibration(node);
    const curve = Math.max(0.000001, Number(valueOf(node, "curve", 1)) || 1);
    const position = clamp(Number(normalized) || 0, -1, 1);
    return position >= 0
        ? center + (high - center) * Math.pow(position, curve)
        : center + (low - center) * Math.pow(-position, curve);
}

function formatSigned(value, digits = 2) {
    const safe = Math.abs(value) < 0.5 * (10 ** -digits) ? 0 : value;
    return `${safe >= 0 ? "+" : ""}${safe.toFixed(digits)}`;
}

function formatActual(value) {
    if (!Number.isFinite(value)) return "—";
    const magnitude = Math.abs(value);
    return value.toFixed(magnitude >= 100 ? 1 : magnitude >= 10 ? 2 : 3);
}

function roundedRect(ctx, x, y, width, height, radius) {
    ctx.beginPath();
    if (ctx.roundRect) {
        ctx.roundRect(x, y, width, height, radius);
        return;
    }
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + width, y, x + width, y + height, radius);
    ctx.arcTo(x + width, y + height, x, y + height, radius);
    ctx.arcTo(x, y + height, x, y, radius);
    ctx.arcTo(x, y, x + width, y, radius);
    ctx.closePath();
}

function drawSlider(ctx, node, width, y) {
    const left = MARGIN;
    const trackWidth = Math.max(80, width - MARGIN * 2);
    const normalized = clamp(Number(this.value) || 0, -1, 1);
    const centerX = left + trackWidth / 2;
    const handleX = left + ((normalized + 1) / 2) * trackWidth;
    const trackY = y + 24;
    const trackHeight = 10;
    const label = String(valueOf(node, "label", "")).trim() || "Slider";
    const showCalibration = node.properties?.showCalibration !== false;

    this._track = { x: left, y: trackY, width: trackWidth, height: trackHeight };

    ctx.save();
    ctx.textBaseline = "middle";
    ctx.font = "600 12px Arial, sans-serif";
    ctx.textAlign = "left";
    ctx.fillStyle = "#e5e5e5";
    ctx.fillText(label.toUpperCase(), left, y + 8);
    ctx.textAlign = "right";
    ctx.fillStyle = "#cfcfcf";
    ctx.fillText(formatSigned(normalized), left + trackWidth, y + 8);

    roundedRect(ctx, left, trackY, trackWidth, trackHeight, trackHeight / 2);
    ctx.fillStyle = "#151515";
    ctx.fill();

    roundedRect(ctx, left, trackY, trackWidth, trackHeight, trackHeight / 2);
    const wash = ctx.createLinearGradient(left, 0, left + trackWidth, 0);
    wash.addColorStop(0, "rgba(220, 63, 54, 0.42)");
    wash.addColorStop(0.5, "rgba(210, 210, 210, 0.08)");
    wash.addColorStop(1, "rgba(55, 194, 98, 0.42)");
    ctx.fillStyle = wash;
    ctx.fill();

    if (Math.abs(normalized) > 0.001) {
        roundedRect(ctx, left, trackY, trackWidth, trackHeight, trackHeight / 2);
        ctx.save();
        ctx.clip();
        ctx.fillStyle = normalized < 0
            ? `rgba(225, 61, 52, ${0.45 + Math.abs(normalized) * 0.45})`
            : `rgba(48, 199, 96, ${0.45 + Math.abs(normalized) * 0.45})`;
        ctx.fillRect(
            Math.min(centerX, handleX),
            trackY,
            Math.abs(handleX - centerX),
            trackHeight,
        );
        ctx.restore();
    }

    ctx.fillStyle = "rgba(255, 255, 255, 0.72)";
    ctx.fillRect(centerX - 1, trackY - 4, 2, trackHeight + 8);

    ctx.beginPath();
    ctx.arc(handleX, trackY + trackHeight / 2, 8, 0, Math.PI * 2);
    ctx.fillStyle = this._dragging ? "#ffffff" : "#e8e8e8";
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(0, 0, 0, 0.7)";
    ctx.stroke();

    const scaleY = trackY + trackHeight + 10;
    ctx.font = "10px Arial, sans-serif";
    ctx.fillStyle = "#929292";
    ctx.textAlign = "left";
    ctx.fillText("-1  LOW", left, scaleY);
    ctx.textAlign = "center";
    ctx.fillText("0", centerX, scaleY);
    ctx.textAlign = "right";
    ctx.fillText("HIGH  +1", left + trackWidth, scaleY);

    if (showCalibration) {
        const actual = mappedValue(node, normalized);
        ctx.font = "11px Arial, sans-serif";
        ctx.fillStyle = "#bcbcbc";
        ctx.textAlign = "left";
        ctx.fillText(`Normalized: ${formatSigned(normalized)}`, left, y + 64);
        ctx.textAlign = "right";
        ctx.fillText(`Actual: ${formatActual(actual)}`, left + trackWidth, y + 64);
    }
    ctx.restore();
}

function setNormalized(node, slider, nextValue, event, pos) {
    const normalized = clamp(nextValue, -1, 1);
    if (normalized === slider.value) return;
    slider.value = normalized;
    slider.callback?.(normalized, app.canvas, node, pos, event);
    node.setDirtyCanvas(true, true);
}

function sliderMouse(event, pos, node) {
    if (event.type === "dblclick") {
        setNormalized(node, this, 0, event, pos);
        return true;
    }

    if (event.type === "pointerdown" || event.type === "mousedown") {
        this._dragging = true;
    } else if (event.type === "pointerup" || event.type === "mouseup") {
        this._dragging = false;
        node.setDirtyCanvas(true, true);
        return true;
    }
    if (!this._dragging) return false;

    const track = this._track;
    const left = track?.x ?? MARGIN;
    const width = track?.width ?? Math.max(80, node.size[0] - MARGIN * 2);
    let normalized = clamp(((pos[0] - left) / width) * 2 - 1, -1, 1);
    normalized = Math.round(normalized * (event.shiftKey ? 1000 : 100)) /
        (event.shiftKey ? 1000 : 100);
    if (!event.shiftKey && Math.abs(normalized) < 0.03) normalized = 0;
    setNormalized(node, this, normalized, event, pos);
    return true;
}

function updateButton(node) {
    if (!node._wnCalibrationButton) return;
    node._wnCalibrationButton.name = "Hide Calibration";
}

function setCalibrationButtonPresence(node, show) {
    const button = node._wnCalibrationButton;
    if (!button || !node.widgets) return;

    const currentIndex = node.widgets.indexOf(button);
    if (show && currentIndex < 0) {
        const savedIndex = Number.isInteger(node._wnCalibrationButtonIndex)
            ? node._wnCalibrationButtonIndex
            : node.widgets.length;
        node.widgets.splice(clamp(savedIndex, 0, node.widgets.length), 0, button);
    } else if (!show && currentIndex >= 0) {
        node._wnCalibrationButtonIndex = currentIndex;
        node.widgets.splice(currentIndex, 1);
    }
}

function setCalibrationVisibility(node) {
    node.properties ??= {};
    const show = node.properties.showCalibration !== false;

    for (const name of CALIBRATION_WIDGETS) {
        const item = widget(node, name);
        if (!item) continue;
        if (show) {
            if (item._wnOriginalType !== undefined) {
                item.type = item._wnOriginalType;
                item.computeSize = item._wnOriginalComputeSize;
                item.draw = item._wnOriginalDraw;
                item.mouse = item._wnOriginalMouse;
                delete item._wnOriginalType;
                delete item._wnOriginalComputeSize;
                delete item._wnOriginalDraw;
                delete item._wnOriginalMouse;
            }
        } else if (item._wnOriginalType === undefined) {
            item._wnOriginalType = item.type;
            item._wnOriginalComputeSize = item.computeSize;
            item._wnOriginalDraw = item.draw;
            item._wnOriginalMouse = item.mouse;
            item.type = "wn_hidden";
            item.computeSize = () => [0, -4];
            item.draw = () => {};
            item.mouse = () => false;
        }
    }

    setCalibrationButtonPresence(node, show);
    updateButton(node);
    node.setSize(node.computeSize());
    node.setDirtyCanvas(true, true);
}

function installCalibrationCallbacks(node) {
    for (const [name, label] of Object.entries(WIDGET_LABELS)) {
        const item = widget(node, name);
        if (item) item.label = label;
    }

    for (const name of ["low_value", "center_value", "high_value", "curve"]) {
        const item = widget(node, name);
        if (!item || item._wnCallbackInstalled) continue;
        const original = item.callback;
        item.callback = function () {
            original?.apply(this, arguments);
            node.setDirtyCanvas(true, true);
        };
        item._wnCallbackInstalled = true;
    }

    const label = widget(node, "label");
    if (label && !label._wnCallbackInstalled) {
        const original = label.callback;
        label.callback = function () {
            original?.apply(this, arguments);
            node.setDirtyCanvas(true, true);
        };
        label._wnCallbackInstalled = true;
    }
}

function replaceNormalizedWidget(node) {
    const index = node.widgets?.findIndex((item) => item.name === "normalized") ?? -1;
    if (index < 0 || node.widgets[index].type === "wn_slider") return;
    const original = node.widgets[index];
    const custom = {
        type: "wn_slider",
        name: "normalized",
        value: Number(original.value) || 0,
        options: { min: -1, max: 1, step: 0.01 },
        callback: original.callback,
        draw: drawSlider,
        mouse: sliderMouse,
        computeSize(width) {
            return [
                width,
                node.properties?.showCalibration === false
                    ? SIMPLE_HEIGHT
                    : CALIBRATION_HEIGHT,
            ];
        },
        serializeValue() {
            return this.value;
        },
    };
    node.widgets[index] = custom;
}

function addCalibrationControls(node) {
    if (node._wnCalibrationButton) return;
    node.properties ??= {};
    if (node.properties.showCalibration === undefined) {
        node.properties.showCalibration = true;
    }

    node._wnCalibrationButton = node.addWidget(
        "button",
        "Hide Calibration",
        null,
        () => {
            node.properties.showCalibration = node.properties.showCalibration === false;
            setCalibrationVisibility(node);
        },
        { serialize: false },
    );
    node._wnCalibrationButton.serialize = false;
}

app.registerExtension({
    name: EXTENSION_NAME,

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            replaceNormalizedWidget(this);
            installCalibrationCallbacks(this);
            addCalibrationControls(this);
            setCalibrationVisibility(this);
            requestAnimationFrame(() => setCalibrationVisibility(this));
            return result;
        };

        const originalConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = originalConfigure?.apply(this, arguments);
            requestAnimationFrame(() => {
                replaceNormalizedWidget(this);
                installCalibrationCallbacks(this);
                addCalibrationControls(this);
                setCalibrationVisibility(this);
            });
            return result;
        };

        const originalMenu = nodeType.prototype.getExtraMenuOptions;
        nodeType.prototype.getExtraMenuOptions = function (_, options) {
            const result = originalMenu?.apply(this, arguments);
            options.unshift(
                {
                    content: "Reset Slider to Center",
                    callback: () => {
                        const item = widget(this, "normalized");
                        if (item) setNormalized(this, item, 0, null, [0, 0]);
                    },
                },
                {
                    content: this.properties?.showCalibration === false
                        ? "Show Calibration"
                        : "Hide Calibration",
                    callback: () => {
                        this.properties.showCalibration =
                            this.properties.showCalibration === false;
                        setCalibrationVisibility(this);
                    },
                },
                null,
            );
            return result;
        };
    },
});
