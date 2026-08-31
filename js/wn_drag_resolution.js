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
    const min = Math.max(div, Math.ceil(MIN_PX / div) * div);
    const max = Math.max(min, Math.floor(MAX_PX / div) * div);
    return clampVal(Math.round(v / div) * div, min, max);
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

function resolutionMP(w, h) {
    return (w * h) / 1_000_000;
}

function findBestResolution(targetMP, targetRatio, divisor) {
    const div = clampVal(Math.round(Number(divisor) || 32), 1, MAX_PX);
    const min = Math.max(div, Math.ceil(MIN_PX / div) * div);
    const max = Math.max(min, Math.floor(MAX_PX / div) * div);
    const maxMP = resolutionMP(max, max);
    const mpValue = Number(targetMP);
    const ratioValue = Number(targetRatio);
    const mp = clampVal(
        Number.isFinite(mpValue) && mpValue > 0 ? mpValue : resolutionMP(min, min),
        Number.EPSILON,
        maxMP
    );
    const ratio = Number.isFinite(ratioValue) && ratioValue > 0 ? ratioValue : 1;
    const targetPixels = mp * 1_000_000;
    const idealW = Math.sqrt(targetPixels * ratio);
    const idealH = Math.sqrt(targetPixels / ratio);

    const candidateAxis = (ideal) => {
        const center = clampVal(Math.round(ideal / div) * div, min, max);
        const values = new Set([min, max]);
        for (let offset = -8; offset <= 8; offset++) {
            values.add(clampVal(center + offset * div, min, max));
        }
        return [...values].sort((a, b) => a - b);
    };

    let best = null;
    let bestKey = null;
    for (const width of candidateAxis(idealW)) {
        for (const height of candidateAxis(idealH)) {
            const areaError = Math.abs(width * height - targetPixels) / targetPixels;
            const ratioError = Math.abs(Math.log((width / height) / ratio));
            const sizeError = Math.pow((width - idealW) / idealW, 2)
                + Math.pow((height - idealH) / idealH, 2);
            const key = [
                areaError + ratioError * 2 + sizeError * 0.1,
                areaError,
                ratioError,
                sizeError,
                width * height,
                width,
                height,
            ];
            if (!bestKey || key.some((value, i) =>
                value < bestKey[i] && key.slice(0, i).every((v, j) => v === bestKey[j])
            )) {
                bestKey = key;
                best = { width, height };
            }
        }
    }
    return best;
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
    return clampVal(Math.round(Number(getWidget(node, "divisor")?.value) || 32), 1, MAX_PX);
}

function currentTargetRatio(node) {
    const preset = parseRatio(getWidget(node, "aspect_ratio")?.value);
    if (preset) return preset;
    const width = Number(getWidget(node, "width")?.value) || 1;
    const height = Number(getWidget(node, "height")?.value) || 1;
    return width > 0 && height > 0 ? width / height : 1;
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
        startRatio: 1,
        lastW: getWidget(node, "width")?.value ?? 1024,
        lastH: getWidget(node, "height")?.value ?? 1024,
        syncing: false,
        cleanupDragListeners: null,
    };
    return node._wnDragResolution;
}

function withSyncGuard(node, fn) {
    const state = initState(node);
    if (state.syncing) return;
    state.syncing = true;
    try {
        fn();
    } finally {
        state.syncing = false;
    }
}

function endDrag(node) {
    const state = initState(node);
    state.drag = null;
    state.startMouse = null;
    state.cleanupDragListeners?.();
    state.cleanupDragListeners = null;
}

function installDragTermination(node) {
    const state = initState(node);
    state.cleanupDragListeners?.();
    const finish = () => endDrag(node);
    const targets = typeof window !== "undefined" ? [window] : [];
    for (const target of targets) {
        target.addEventListener("mouseup", finish, true);
        target.addEventListener("pointerup", finish, true);
        target.addEventListener("pointercancel", finish, true);
        target.addEventListener("blur", finish, true);
    }
    state.cleanupDragListeners = () => {
        for (const target of targets) {
            target.removeEventListener("mouseup", finish, true);
            target.removeEventListener("pointerup", finish, true);
            target.removeEventListener("pointercancel", finish, true);
            target.removeEventListener("blur", finish, true);
        }
    };
}

function setResolution(node, width, height, updateMP = true) {
    const state = initState(node);
    const widthWidget = getWidget(node, "width");
    const heightWidget = getWidget(node, "height");
    const mpWidget = getWidget(node, "target_mp");
    if (!widthWidget || !heightWidget) return;
    widthWidget.value = width;
    heightWidget.value = height;
    state.lastW = width;
    state.lastH = height;
    if (updateMP && mpWidget) {
        mpWidget.value = Number(resolutionMP(width, height).toFixed(6));
    }
    node.setDirtyCanvas(true, true);
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

    return null;
}

function cornerResize(desiredW, desiredH, targetRatio, div) {
    const desiredPixels = Math.max(1, desiredW * desiredH);
    return findBestResolution(desiredPixels / 1_000_000, targetRatio, div);
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
    const wMP = getWidget(node, "target_mp");
    if (wMP) wMP.label = "Target MP";

    if (wW) {
        const orig = wW.callback;
        wW.callback = function (value) {
            orig?.call(this, value);
            withSyncGuard(node, () => {
                const width = normalizeArrowStep(node, wW, state.lastW);
                const div = getDivisor(node);
                const ratio = parseRatio(wAR?.value);
                let height = Number(wH?.value) || div;
                if (ratio && wH) height = snapVal(width / ratio, div);
                setResolution(node, width, height, true);
            });
        };
    }

    if (wH) {
        const orig = wH.callback;
        wH.callback = function (value) {
            orig?.call(this, value);
            withSyncGuard(node, () => {
                const height = normalizeArrowStep(node, wH, state.lastH);
                const div = getDivisor(node);
                const ratio = parseRatio(wAR?.value);
                let width = Number(wW?.value) || div;
                if (ratio && wW) width = snapVal(height * ratio, div);
                setResolution(node, width, height, true);
            });
        };
    }

    if (wDV) {
        const orig = wDV.callback;
        wDV.callback = function (value) {
            orig?.call(this, value);
            withSyncGuard(node, () => {
                syncResolutionSteps(node);
                if (!wW || !wH) return;
                const mp = resolutionMP(Number(wW.value) || 1, Number(wH.value) || 1);
                const result = findBestResolution(mp, currentTargetRatio(node), getDivisor(node));
                setResolution(node, result.width, result.height, true);
            });
        };
    }

    if (wAR) {
        const orig = wAR.callback;
        wAR.callback = function (value) {
            orig?.call(this, value);
            withSyncGuard(node, () => {
                const ratio = parseRatio(value);
                if (!ratio || !wW || !wH) {
                    node.setDirtyCanvas(true, true);
                    return;
                }
                const mp = resolutionMP(Number(wW.value) || 1, Number(wH.value) || 1);
                const result = findBestResolution(mp, ratio, getDivisor(node));
                setResolution(node, result.width, result.height, true);
            });
        };
    }

    if (wMP) {
        const orig = wMP.callback;
        wMP.callback = function (value) {
            orig?.call(this, value);
            withSyncGuard(node, () => {
                const mp = clampVal(Number(value) || 0.01, 0.01, 67.0);
                wMP.value = mp;
                const result = findBestResolution(mp, currentTargetRatio(node), getDivisor(node));
                setResolution(node, result.width, result.height, false);
            });
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
    const showMP = bw >= 70 && bh >= 52;
    const showRatio = bw >= 100 && bh >= 76;
    ctx.fillText(`${pixW} × ${pixH}`, cx, cy - (showRatio ? 16 : showMP ? 9 : 0));

    ctx.fillStyle = COL_SUB;
    ctx.font = "11px sans-serif";
    if (showMP) {
        ctx.fillText(`${resolutionMP(pixW, pixH).toFixed(3)} MP`, cx, cy + (showRatio ? 0 : 9));
    }
    const preset = getWidget(node, "aspect_ratio")?.value || "Free";
    const actual = ratioStr(pixW, pixH);
    const ratioLabel = preset === "Free"
        ? `Free · ${actual}`
        : (Math.abs((pixW / pixH) / parseRatio(preset) - 1) < 0.001
            ? `target ${preset}`
            : `${preset} → ${actual}`);
    if (showRatio) ctx.fillText(ratioLabel, cx, cy + 16);
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
            const width = Number(getWidget(this, "width")?.value) || 1024;
            const height = Number(getWidget(this, "height")?.value) || 1024;
            const mpWidget = getWidget(this, "target_mp");
            if (mpWidget) {
                mpWidget.value = Number(resolutionMP(width, height).toFixed(6));
            }
            ensureNodeSize(this);
            requestAnimationFrame(() => {
                ensureNodeSize(this);
                this.setDirtyCanvas(true, true);
            });
        };

        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const configuredWidgetCount = arguments[0]?.widgets_values?.length ?? 0;
            origOnConfigure?.apply(this, arguments);
            initState(this);
            requestAnimationFrame(() => {
                installWidgetCallbacks(this);
                const state = initState(this);
                const width = Number(getWidget(this, "width")?.value) || 1024;
                const height = Number(getWidget(this, "height")?.value) || 1024;
                state.lastW = width;
                state.lastH = height;
                if (configuredWidgetCount < 5) {
                    const mpWidget = getWidget(this, "target_mp");
                    if (mpWidget) mpWidget.value = Number(resolutionMP(width, height).toFixed(6));
                }
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
            if (e && "button" in e && e.button !== 0) {
                return origMouseDown?.apply(this, arguments);
            }
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
                state.startRatio = currentTargetRatio(this);
                state.lastW = state.startW;
                state.lastH = state.startH;

                withSyncGuard(this, () => setResolution(
                    this, state.startW, state.startH, true
                ));

                installDragTermination(this);
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

            if (e && "buttons" in e && !(e.buttons & 1)) {
                endDrag(this);
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
                const resized = cornerResize(newW, newH, state.startRatio, div);
                newW = resized.width;
                newH = resized.height;
            } else {
                newW = snapVal(newW, div);
                newH = snapVal(newH, div);
            }

            newW = clampVal(newW, div, MAX_PX);
            newH = clampVal(newH, div, MAX_PX);

            withSyncGuard(this, () => {
                if (isEdgeDrag(state.drag)) setAspectPresetFree(this);
                setResolution(this, newW, newH, true);
            });
            return true;
        };

        const origMouseUp = nodeType.prototype.onMouseUp;
        nodeType.prototype.onMouseUp = function () {
            const state = initState(this);
            if (state.drag) {
                endDrag(this);
                return true;
            }

            return origMouseUp?.apply(this, arguments);
        };

        const origOnRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            endDrag(this);
            return origOnRemoved?.apply(this, arguments);
        };
    },
});
