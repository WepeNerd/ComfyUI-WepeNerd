import { app } from "../../scripts/app.js";
import { POSITIONS, cellOf, keptRect, labelOf, valueForKey, valueFromPoint } from "./zoom_crop.mjs?v=2";

const NODE_NAME = "WN_ZoomCrop";
const ACCENT = "#5a96ff";
const HEIGHT = 168;

const CSS = `
.wn-zc{display:flex;flex-direction:column;align-items:center;gap:4px;width:100%;height:100%;box-sizing:border-box;padding:4px 0;font:11px sans-serif;color:#bbb;user-select:none}
.wn-zc *{box-sizing:border-box}
.wn-zc-frame{position:relative;width:176px;height:132px;flex-shrink:0;border:1px solid #4a4b50;border-radius:4px;cursor:pointer;touch-action:none;outline:none;overflow:hidden;
background:repeating-linear-gradient(135deg,#1d1e21 0 6px,#232428 6px 12px)}
.wn-zc-frame:focus-visible{border-color:${ACCENT}}
.wn-zc-keep,.wn-zc-ghost{position:absolute;pointer-events:none;border-radius:2px}
.wn-zc-keep{background:rgba(90,150,255,.22);border:2px solid ${ACCENT};box-shadow:0 0 0 999px rgba(0,0,0,.35);transition:left .12s ease,top .12s ease}
.wn-zc-ghost{border:1px dashed rgba(255,255,255,.55);display:none}
.wn-zc-dot{position:absolute;width:6px;height:6px;margin:-3px 0 0 -3px;border-radius:50%;background:rgba(255,255,255,.28);pointer-events:none;transition:transform .1s}
.wn-zc-dot.hover{background:rgba(255,255,255,.75);transform:scale(1.4)}
.wn-zc-dot.on{background:#fff;transform:scale(1.7)}
.wn-zc-label{height:16px;line-height:16px;white-space:nowrap}
.wn-zc-label b{color:#e6e6e6;font-weight:600}
.wn-zc.linked{opacity:.4;pointer-events:none}
`;

function injectStyle() {
    if (document.getElementById("wn-zoom-crop-style")) return;
    const style = document.createElement("style");
    style.id = "wn-zoom-crop-style";
    style.textContent = CSS;
    document.head.append(style);
}

function element(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
}

function place(box, value) {
    const rect = keptRect(value);
    box.style.left = `${rect.left * 100}%`;
    box.style.top = `${rect.top * 100}%`;
    box.style.width = `${rect.width * 100}%`;
    box.style.height = `${rect.height * 100}%`;
}

function setupZoomCrop(node) {
    if (node._wnZoomCrop) return;
    const widget = node.widgets?.find((item) => item.name === "position");
    if (!widget) return;
    injectStyle();

    // Keep the combo for saving/API use but take it out of the UI, so clicking
    // never opens its 25-item dropdown.
    Object.assign(widget, {
        type: "wn_hidden", hidden: true, computeSize: () => [0, -4],
        draw() {}, mouse: () => false, onClick() {},
    });

    const root = element("div", "wn-zc");
    const frame = element("div", "wn-zc-frame");
    frame.tabIndex = 0;
    frame.title = "Click or drag to choose which part of the image to keep. Arrow keys nudge, C centers.";
    const keep = element("div", "wn-zc-keep");
    const ghost = element("div", "wn-zc-ghost");
    const dots = POSITIONS.map((value, index) => {
        const dot = element("div", "wn-zc-dot");
        dot.style.left = `${((index % 5) + 0.5) * 20}%`;
        dot.style.top = `${(Math.floor(index / 5) + 0.5) * 20}%`;
        frame.append(dot);
        return dot;
    });
    const label = element("div", "wn-zc-label");
    frame.append(keep, ghost);
    root.append(frame, label);

    const dom = node.addDOMWidget("position_picker", "wn_zoom_crop", root, {
        serialize: false, hideOnZoom: false,
        getMinHeight: () => HEIGHT, getMaxHeight: () => HEIGHT,
    });
    dom.computeSize = (width) => [width ?? 200, HEIGHT];

    let hover = null;
    let dragging = false;

    const current = () => (POSITIONS.includes(widget.value) ? widget.value : "center");

    function linked() {
        return node.inputs?.some((input) => input.widget?.name === "position" && input.link != null) ?? false;
    }

    function sync() {
        const value = current();
        place(keep, value);
        const { column, row } = cellOf(value);
        const selected = row * 5 + column;
        const hovered = hover ? POSITIONS.indexOf(hover) : -1;
        dots.forEach((dot, index) => {
            dot.classList.toggle("on", index === selected);
            dot.classList.toggle("hover", index === hovered && index !== selected);
        });
        if (hover && hover !== value && !dragging) {
            place(ghost, hover);
            ghost.style.display = "block";
        } else {
            ghost.style.display = "none";
        }
        label.innerHTML = "";
        label.append("Keep: ");
        const name = element("b");
        name.textContent = labelOf(hover && !dragging ? hover : value);
        label.append(name);
        root.classList.toggle("linked", linked());
    }

    // A whole drag is one undo step: pointerdown opens it, release closes it.
    function select(value) {
        if (!value || value === widget.value) return;
        if (!dragging) node.graph?.beforeChange?.();
        widget.value = value;
        widget.callback?.(value, app.canvas, node);
        if (!dragging) node.graph?.afterChange?.();
        node.setDirtyCanvas(true, true);
    }

    function pointValue(event) {
        const box = frame.getBoundingClientRect();
        return valueFromPoint(event.clientX - box.left, event.clientY - box.top, box.width, box.height);
    }

    frame.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        frame.focus({ preventScroll: true });
        dragging = true;
        node.graph?.beforeChange?.();
        frame.setPointerCapture?.(event.pointerId);
        select(pointValue(event));
        sync();
    });
    frame.addEventListener("pointermove", (event) => {
        const value = pointValue(event);
        if (dragging) select(value);
        else hover = value;
        sync();
    });
    const release = () => {
        if (!dragging) return;
        dragging = false;
        node.graph?.afterChange?.();
        sync();
    };
    frame.addEventListener("pointerup", release);
    frame.addEventListener("pointercancel", release);
    frame.addEventListener("pointerleave", () => { hover = null; if (!dragging) sync(); });
    frame.addEventListener("keydown", (event) => {
        const value = valueForKey(current(), event.key);
        if (!value) return;
        event.preventDefault();
        event.stopPropagation();
        select(value);
        sync();
    });

    node._wnZoomCrop = { sync };
    sync();
    node.setSize([Math.max(node.size?.[0] ?? 0, 210), node.computeSize()[1]]);
}

app.registerExtension({
    name: "WepeNerd.ZoomCrop",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            setupZoomCrop(this);
            return result;
        };
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = configure?.apply(this, arguments);
            setupZoomCrop(this);
            requestAnimationFrame(() => this._wnZoomCrop?.sync());
            return result;
        };
        const connections = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = connections?.apply(this, arguments);
            this._wnZoomCrop?.sync();
            return result;
        };
    },
});
