import { app } from "../../scripts/app.js";

const EXT_NAME = "WepeNerd.DragResolution";
const NODE_NAME = "WN_DragResolution";

const CANVAS_H = 200;
const PAD = 10;
const HANDLE_R = 6;
const HIT_R = 14;
const MIN_PX = 64;
const MAX_PX = 8192;

const COL_BG = "rgba(0,0,0,0.3)";
const COL_GRID = "rgba(255,255,255,0.05)";
const COL_BOX = "rgba(90,150,255,0.30)";
const COL_BORDER = "rgba(90,150,255,0.85)";
const COL_HANDLE = "rgba(255,255,255,0.90)";
const COL_TEXT = "rgba(255,255,255,0.90)";
const COL_SUB = "rgba(255,255,255,0.55)";

function snapVal(v, div) {
    return Math.max(div, Math.round(v / div) * div);
}

function clampVal(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
}

function dist(x1, y1, x2, y2) {
    return Math.hypot(x1 - x2, y1 - y2);
}

function gcd(a, b) {
    a = Math.abs(Math.round(a));
    b = Math.abs(Math.round(b));
    while (b) {
        [a, b] = [b, a % b];
    }
    return a || 1;
}

function ratioStr(w, h) {
    const g = gcd(w, h);
    return `${w / g}:${h / g}`;
}

function parseRatio(str) {
    if (!str || str === "Free") return null;
    const parts = str.split(":");
    if (parts.length !== 2) return null;

    const a = parseFloat(parts[0]);
    const b = parseFloat(parts[1]);
    return isNaN(a) || isNaN(b) || b === 0 ? null : a / b;
}

function isCornerDrag(mode) {
    return mode === "br" || mode === "tr" || mode === "bl" || mode === "tl";
}

function isEdgeDrag(mode) {
    return mode === "r" || mode === "l" || mode === "b" || mode === "t";
}

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function getDivisor(node) {
    return Number(getWidget(node, "divisor")?.value) || 32;
}

function setNumberStep(widget, step) {
    if (!widget) return;
    widget.options ??= {};
    widget.options.step = step;
    widget.step = step;
}

function syncResolutionSteps(node) {
    const div = getDivisor(node);
    setNumberStep(getWidget(node, "width"), div);
    setNumberStep(getWidget(node, "height"), div);
}

function setAspectPresetFree(node) {
    const widget = getWidget(node, "aspect_ratio");
    if (widget && widget.value !== "Free") {
        widget.value = "Free";
    }
}

function handlePositions(bx, by, bw, bh) {
    return {
        br: [bx + bw, by + bh],
        tr: [bx + bw, by],
        bl: [bx, by + bh],
        tl: [bx, by],
        r: [bx + bw, by + bh / 2],
        l: [bx, by + bh / 2],
        b: [bx + bw / 2, by + bh],
        t: [bx + bw / 2, by],
    };
}

function initState(node) {
    node._wnDragResolution ??= {
        drag: null,
        startMouse: null,
        startW: 0,
        startH: 0,
        lastW: getWidget(node, "width")?.value ?? 1024,
        lastH: getWidget(node, "height")?.value ?? 1024,
    };
    return node._wnDragResolution;
}

function canvasTop(node) {
    let y = 0;

    if (node.outputs?.length) {
        y += node.outputs.length * (LiteGraph.NODE_SLOT_HEIGHT || 20);
    }

    for (const widget of node.widgets ?? []) {
        const size = widget.computeSize
            ? widget.computeSize(node.size[0])
            : [node.size[0], LiteGraph.NODE_WIDGET_HEIGHT || 20];
        y += size[1] + 4;
    }

    return y + 10;
}

function boxLayout(node) {
    const wW = getWidget(node, "width");
    const wH = getWidget(node, "height");
    if (!wW || !wH) return null;

    const pixW = Number(wW.value) || 1;
    const pixH = Number(wH.value) || 1;
    const top = canvasTop(node);
    const areaW = Math.max(80, node.size[0] - PAD * 2);
    const areaH = CANVAS_H;
    const margin = 24;
    const availW = Math.max(1, areaW - margin * 2);
    const availH = Math.max(1, areaH - margin * 2);
    const scale = Math.min(availW / pixW, availH / pixH);
    const bw = pixW * scale;
    const bh = pixH * scale;
    const bx = PAD + (areaW - bw) / 2;
    const by = top + (areaH - bh) / 2;

    return { bx, by, bw, bh, scale, top, areaW, areaH, pixW, pixH };
}

function hitTest(node, localX, localY) {
    const layout = boxLayout(node);
    if (!layout) return null;

    const { bx, by, bw, bh } = layout;
    const handles = handlePositions(bx, by, bw, bh);

    for (const key of ["br", "tr", "bl", "tl", "r", "l", "b", "t"]) {
        const [hx, hy] = handles[key];
        if (dist(localX, localY, hx, hy) < HIT_R) return key;
    }

    if (localX >= bx && localX <= bx + bw && localY >= by && localY <= by + bh) {
        return "move";
    }

    return null;
}

function cornerResize(startW, startH, desiredW, desiredH, dx, dy, div) {
    const unitW = Math.max(1, Math.round(startW / div));
    const unitH = Math.max(1, Math.round(startH / div));
    const unitGcd = gcd(unitW, unitH);
    const ratioUnitW = Math.max(1, unitW / unitGcd);
    const ratioUnitH = Math.max(1, unitH / unitGcd);
    const baseW = ratioUnitW * div;
    const baseH = ratioUnitH * div;
    const useWidth = Math.abs(dx) >= Math.abs(dy);
    const rawSteps = useWidth ? desiredW / baseW : desiredH / baseH;
    const minSteps = Math.max(
        1,
        Math.ceil(MIN_PX / baseW),
        Math.ceil(MIN_PX / baseH)
    );
    const maxSteps = Math.max(
        minSteps,
        Math.floor(Math.min(MAX_PX / baseW, MAX_PX / baseH))
    );
    const steps = clampVal(Math.round(rawSteps), minSteps, maxSteps);

    return {
        width: baseW * steps,
        height: baseH * steps,
    };
}

function normalizeArrowStep(node, widget, previous) {
    const div = getDivisor(node);
    const raw = Number(widget.value) || previous || div;
    let next = snapVal(raw, div);

    if (raw > previous && next <= previous) {
        next = snapVal(previous + div, div);
    } else if (raw < previous && next >= previous) {
        next = snapVal(previous - div, div);
    }

    next = clampVal(next, div, MAX_PX);
    widget.value = next;
    return next;
}

function installWidgetCallbacks(node) {
    if (node._wnDragCallbacksInstalled) return;
    node._wnDragCallbacksInstalled = true;

    const state = initState(node);
    const wW = getWidget(node, "width");
    const wH = getWidget(node, "height");
    const wAR = getWidget(node, "aspect_ratio");
    const wDV = getWidget(node, "divisor");

    if (wW) {
        const orig = wW.callback;
        wW.callback = function (value) {
            orig?.call(this, value);
            state.lastW = normalizeArrowStep(node, wW, state.lastW);
            node.setDirtyCanvas(true, true);
        };
    }

    if (wH) {
        const orig = wH.callback;
        wH.callback = function (value) {
            orig?.call(this, value);
            state.lastH = normalizeArrowStep(node, wH, state.lastH);
            node.setDirtyCanvas(true, true);
        };
    }

    if (wDV) {
        const orig = wDV.callback;
        wDV.callback = function (value) {
            orig?.call(this, value);
            syncResolutionSteps(node);
            node.setDirtyCanvas(true, true);
        };
    }

    if (wAR) {
        const orig = wAR.callback;
        wAR.callback = function (value) {
            orig?.call(this, value);

            const ratio = parseRatio(value);
            const div = getDivisor(node);
            if (ratio && wW && wH) {
                wH.value = clampVal(snapVal(wW.value / ratio, div), div, MAX_PX);
                state.lastW = wW.value;
                state.lastH = wH.value;
                node.setDirtyCanvas(true, true);
            }
        };
    }

    syncResolutionSteps(node);
}

function ensureNodeSize(node) {
    if (node.size[0] < 320) node.size[0] = 320;

    const needed = canvasTop(node) + CANVAS_H + 10;
    if (node.size[1] < needed) {
        node.size[1] = needed;
    }
}

function drawResolutionPicker(node, ctx) {
    const layout = boxLayout(node);
    if (!layout) return;

    ensureNodeSize(node);

    const { bx, by, bw, bh, scale, top, areaW, areaH, pixW, pixH } = layout;

    ctx.fillStyle = COL_BG;
    ctx.beginPath();
    if (ctx.roundRect) {
        ctx.roundRect(PAD, top, areaW, areaH, 6);
    } else {
        ctx.rect(PAD, top, areaW, areaH);
    }
    ctx.fill();

    const div = getDivisor(node);
    const gridStep = div * scale;
    if (gridStep > 3) {
        ctx.strokeStyle = COL_GRID;
        ctx.lineWidth = 1;

        for (let gx = bx + gridStep; gx < bx + bw - 1; gx += gridStep) {
            ctx.beginPath();
            ctx.moveTo(gx, by);
            ctx.lineTo(gx, by + bh);
            ctx.stroke();
        }

        for (let gy = by + gridStep; gy < by + bh - 1; gy += gridStep) {
            ctx.beginPath();
            ctx.moveTo(bx, gy);
            ctx.lineTo(bx + bw, gy);
            ctx.stroke();
        }
    }

    ctx.fillStyle = COL_BOX;
    ctx.strokeStyle = COL_BORDER;
    ctx.lineWidth = 2;
    ctx.beginPath();
    if (ctx.roundRect) {
        ctx.roundRect(bx, by, bw, bh, 3);
    } else {
        ctx.rect(bx, by, bw, bh);
    }
    ctx.fill();
    ctx.stroke();

    const handles = handlePositions(bx, by, bw, bh);
    ctx.fillStyle = COL_HANDLE;
    for (const handle of Object.values(handles)) {
        ctx.beginPath();
        ctx.arc(handle[0], handle[1], HANDLE_R, 0, Math.PI * 2);
        ctx.fill();
    }

    const cx = bx + bw / 2;
    const cy = by + bh / 2;

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = COL_TEXT;
    ctx.font = "bold 13px sans-serif";
    ctx.fillText(`${pixW} x ${pixH}`, cx, cy - 8);

    ctx.fillStyle = COL_SUB;
    ctx.font = "11px sans-serif";
    ctx.fillText(ratioStr(pixW, pixH), cx, cy + 9);
}

app.registerExtension({
    name: EXT_NAME,

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated?.apply(this, arguments);
            initState(this);
            installWidgetCallbacks(this);
            ensureNodeSize(this);
            requestAnimationFrame(() => {
                ensureNodeSize(this);
                this.setDirtyCanvas(true, true);
            });
        };

        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            origOnConfigure?.apply(this, arguments);
            initState(this);
            requestAnimationFrame(() => {
                installWidgetCallbacks(this);
                ensureNodeSize(this);
                this.setDirtyCanvas(true, true);
            });
        };

        const origDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            origDrawForeground?.apply(this, arguments);
            installWidgetCallbacks(this);
            drawResolutionPicker(this, ctx);
        };

        const origMouseDown = nodeType.prototype.onMouseDown;
        nodeType.prototype.onMouseDown = function (e, localPos) {
            const hit = hitTest(this, localPos[0], localPos[1]);
            if (hit) {
                const state = initState(this);
                const div = getDivisor(this);
                const wW = getWidget(this, "width");
                const wH = getWidget(this, "height");

                state.drag = hit;
                state.startMouse = [localPos[0], localPos[1]];
                state.startW = snapVal(Number(wW?.value) || 1024, div);
                state.startH = snapVal(Number(wH?.value) || 1024, div);
                state.lastW = state.startW;
                state.lastH = state.startH;

                if (wW) wW.value = state.startW;
                if (wH) wH.value = state.startH;

                this.setDirtyCanvas(true, true);
                return true;
            }

            return origMouseDown?.apply(this, arguments);
        };

        const origMouseMove = nodeType.prototype.onMouseMove;
        nodeType.prototype.onMouseMove = function (e, localPos) {
            const state = initState(this);
            if (!state.drag) {
                return origMouseMove?.apply(this, arguments);
            }

            if (state.drag === "move") {
                return true;
            }

            const layout = boxLayout(this);
            const wW = getWidget(this, "width");
            const wH = getWidget(this, "height");
            if (!layout || !wW || !wH) return true;

            const dx = localPos[0] - state.startMouse[0];
            const dy = localPos[1] - state.startMouse[1];
            const div = getDivisor(this);
            const pxPerCanvasPx = 1 / layout.scale;
            let newW = state.startW;
            let newH = state.startH;

            if (state.drag === "br" || state.drag === "r" || state.drag === "tr") {
                newW = state.startW + dx * pxPerCanvasPx;
            }
            if (state.drag === "br" || state.drag === "b" || state.drag === "bl") {
                newH = state.startH + dy * pxPerCanvasPx;
            }
            if (state.drag === "tl" || state.drag === "l" || state.drag === "bl") {
                newW = state.startW - dx * pxPerCanvasPx;
            }
            if (state.drag === "tl" || state.drag === "t" || state.drag === "tr") {
                newH = state.startH - dy * pxPerCanvasPx;
            }

            newW = clampVal(newW, MIN_PX, MAX_PX);
            newH = clampVal(newH, MIN_PX, MAX_PX);

            if (isCornerDrag(state.drag)) {
                const resized = cornerResize(state.startW, state.startH, newW, newH, dx, dy, div);
                newW = resized.width;
                newH = resized.height;
            } else {
                newW = snapVal(newW, div);
                newH = snapVal(newH, div);

                if (isEdgeDrag(state.drag)) {
                    setAspectPresetFree(this);
                }
            }

            newW = clampVal(newW, div, MAX_PX);
            newH = clampVal(newH, div, MAX_PX);

            wW.value = newW;
            wH.value = newH;
            state.lastW = newW;
            state.lastH = newH;
            this.setDirtyCanvas(true, true);
            return true;
        };

        const origMouseUp = nodeType.prototype.onMouseUp;
        nodeType.prototype.onMouseUp = function () {
            const state = initState(this);
            if (state.drag) {
                state.drag = null;
                state.startMouse = null;
                return true;
            }

            return origMouseUp?.apply(this, arguments);
        };
    },
});
