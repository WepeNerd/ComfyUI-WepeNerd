import { app } from "../../scripts/app.js";
import {
    MIN_GAP, MAX_POINTS, MODES, PRESETS, DEFAULT_CURVE, fitCurve, makeEvaluator,
    normalizePoints, parseCurve, round, samplePositions, sampleSigmas, serializeCurve,
} from "./sigma_curve.mjs?v=1";

const NODE_NAME = "WN_SigmaCurve";
const ACCENT = "#5a96ff";
const HIT_R = 9;
const DELETE_DISTANCE = 40;
const HISTORY_LIMIT = 100;
const M = { l: 46, r: 12, t: 12, b: 22 };

const CSS = `
.wn-sigma{display:flex;flex-direction:column;gap:4px;width:100%;height:100%;box-sizing:border-box;padding:4px 2px;font:11px sans-serif;color:#ccc;outline:none}
.wn-sigma *{box-sizing:border-box}
.wn-sigma .sc-bar{display:flex;gap:4px;align-items:center;min-width:0}
.wn-sigma button,.wn-sigma select{background:#303134;color:#ddd;border:1px solid #4a4b50;border-radius:4px;padding:3px 7px;font:inherit;cursor:pointer;height:22px}
.wn-sigma button:hover,.wn-sigma select:hover{border-color:#6a6b72}
.wn-sigma button:disabled{opacity:.4;cursor:default}
.wn-sigma button.on{background:#2c4470;border-color:${ACCENT};color:#fff}
.wn-sigma select{min-width:0;flex:0 1 150px}
.wn-sigma .sc-grow{flex:1}
.wn-sigma .sc-stage{position:relative;flex:1;min-height:150px;border:1px solid #3c3d42;border-radius:4px;background:#1b1c1f;overflow:hidden}
.wn-sigma canvas{position:absolute;inset:0;width:100%;height:100%;touch-action:none;cursor:crosshair;display:block}
.wn-sigma .sc-foot{display:flex;gap:6px;align-items:center;min-height:22px;white-space:nowrap}
.wn-sigma .sc-foot label{display:flex;gap:3px;align-items:center;color:#999}
.wn-sigma .sc-foot input{width:64px;background:#222326;color:#eee;border:1px solid #4a4b50;border-radius:3px;padding:2px 4px;font:inherit;height:20px}
.wn-sigma .sc-hint{color:#888;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1;text-align:right}
`;

function injectStyle() {
    if (document.getElementById("wn-sigma-style")) return;
    const style = document.createElement("style");
    style.id = "wn-sigma-style"; style.textContent = CSS;
    document.head.append(style);
}

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

function button(text, title) {
    const b = el("button", null, text); b.title = title; b.type = "button"; return b;
}

const widget = (node, name) => node.widgets?.find((w) => w.name === name);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function fmt(v) {
    if (v === 0) return "0";
    const a = Math.abs(v);
    if (a >= 100) return v.toFixed(1);
    if (a >= 10) return v.toFixed(2);
    if (a >= 1) return v.toFixed(3);
    if (a >= 0.01) return v.toFixed(4);
    return v.toPrecision(3);
}

const tick = (v) => String(parseFloat(v.toPrecision(3)));

function niceStep(range, target) {
    const raw = range / Math.max(1, target), p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
    return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p;
}

function setupSigmaCurve(node) {
    if (node._wnSigma) return;
    injectStyle();

    const curveW = widget(node, "curve");
    if (!curveW) return;
    curveW.hidden = true; curveW.computeSize = () => [0, -4]; curveW.type = "wn_hidden_sigma_curve";

    // ---- DOM -------------------------------------------------------------
    const root = el("div", "wn-sigma"); root.tabIndex = 0;
    const bar = el("div", "sc-bar");
    const preset = el("select"); preset.title = "Replace the curve with a standard schedule (uses sigma_min / sigma_max)";
    preset.append(new Option("Preset…", ""));
    for (const [key, p] of Object.entries(PRESETS)) preset.append(new Option(p.label, key));
    const logBtn = button("Log", "Log-scale sigma axis (view only) — shows detail at low sigmas");
    const snapBtn = button("Snap", "Snap points to step positions (hold Shift while dragging to invert)");
    const undoBtn = button("↶", "Undo (Ctrl/Cmd+Z)"), redoBtn = button("↷", "Redo (Ctrl/Cmd+Shift+Z)");
    const copyBtn = button("Copy", "Copy the output sigmas as a comma-separated list");
    const pasteBtn = button("Paste", "Paste a sigma list: one control point per value, steps set to match");
    bar.append(preset, logBtn, snapBtn, el("span", "sc-grow"), copyBtn, pasteBtn, undoBtn, redoBtn);

    const stage = el("div", "sc-stage");
    const canvas = el("canvas"); stage.append(canvas);

    const foot = el("div", "sc-foot");
    const editor = el("span"); editor.style.cssText = "display:flex;gap:6px;align-items:center";
    const stepLbl = el("label", null, "step"), sigLbl = el("label", null, "σ");
    const stepIn = el("input"), sigIn = el("input");
    for (const i of [stepIn, sigIn]) { i.type = "number"; i.step = "any"; }
    stepLbl.append(stepIn); sigLbl.append(sigIn);
    const delBtn = button("✕", "Delete point (Del)");
    editor.append(stepLbl, sigLbl, delBtn);
    const hint = el("span", "sc-hint");
    foot.append(editor, hint);
    root.append(bar, stage, foot);

    node.addDOMWidget("sigma_curve_editor", "wn_sigma_curve", root, {
        serialize: false, hideOnZoom: false, getMinHeight: () => 250,
    });

    // ---- state -----------------------------------------------------------
    const ctx = canvas.getContext("2d");
    let state = parseCurve(curveW.value || DEFAULT_CURVE);
    let lastSerialized = null;
    let selected = -1, hover = -1, hoverPx = null, drag = null, removed = false, frame = 0;
    let undoStack = [], redoStack = [], signature = "";

    const params = () => {
        const steps = Math.max(1, Math.round(Number(widget(node, "steps")?.value) || 20));
        const sMax = Math.max(1e-4, Number(widget(node, "sigma_max")?.value) || 14.614642);
        const sMin = clamp(Number(widget(node, "sigma_min")?.value) || 0.03, 1e-6, sMax);
        const mode = MODES.includes(widget(node, "interpolation")?.value) ? widget(node, "interpolation").value : "log smooth";
        const endZero = widget(node, "end_at_zero")?.value !== false;
        return { steps, sMax, sMin, mode, endZero, positions: samplePositions(steps, endZero) };
    };

    function commit() {
        lastSerialized = serializeCurve(state);
        if (curveW.value !== lastSerialized) {
            curveW.value = lastSerialized;
            curveW.callback?.(lastSerialized);
        }
        node.setDirtyCanvas(true, false);
        redraw();
    }

    const snapshot = () => ({ points: state.points.map((p) => [...p]), mode: widget(node, "interpolation")?.value });
    function pushHistory() {
        undoStack.push(snapshot());
        if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
        redoStack = [];
    }
    function restore(entry) {
        state.points = entry.points.map((p) => [...p]);
        const mw = widget(node, "interpolation");
        if (mw && entry.mode && mw.value !== entry.mode) mw.value = entry.mode;
        selected = Math.min(selected, state.points.length - 1);
        commit();
    }
    function undo() { if (undoStack.length) { redoStack.push(snapshot()); restore(undoStack.pop()); } }
    function redo() { if (redoStack.length) { undoStack.push(snapshot()); restore(redoStack.pop()); } }

    // ---- geometry --------------------------------------------------------
    let W = 0, H = 0;
    const plot = () => ({ x: M.l, y: M.t, w: Math.max(10, W - M.l - M.r), h: Math.max(10, H - M.t - M.b) });
    function yRange(p) { return Math.min(1, p.sMin / p.sMax) / 2; }
    function yToPx(y, p, r = plot()) {
        if (!state.log) return r.y + (1 - y) * r.h;
        const lo = Math.log(yRange(p));
        const f = y <= 0 ? 0 : clamp((Math.log(y) - lo) / -lo, 0, 1);
        return r.y + (1 - f) * r.h;
    }
    function pxToY(py, p, r = plot()) {
        const f = 1 - (py - r.y) / r.h;
        if (!state.log) return clamp(f, 0, 1);
        if (f < -0.03) return 0;
        const lo = Math.log(yRange(p));
        return clamp(Math.exp(lo + clamp(f, 0, 1) * -lo), 0, 1);
    }
    const xToPx = (x, r = plot()) => r.x + x * r.w;
    const pxToX = (px, r = plot()) => clamp((px - r.x) / r.w, 0, 1);
    const stepOf = (x, p) => 1 + x * (p.positions.length - 1);      // 1-indexed, fractional
    const xOfStep = (s, p) => (p.positions.length > 1 ? (s - 1) / (p.positions.length - 1) : 0);

    function localPos(e) {
        const rect = canvas.getBoundingClientRect();
        const sx = canvas.clientWidth / (rect.width || 1), sy = canvas.clientHeight / (rect.height || 1);
        return [(e.clientX - rect.left) * sx, (e.clientY - rect.top) * sy];
    }
    function hitPoint(px, py, p) {
        let best = -1, bestD = HIT_R;
        state.points.forEach(([x, y], i) => {
            const d = Math.hypot(xToPx(x) - px, yToPx(y, p) - py);
            if (d <= bestD) { best = i; bestD = d; }
        });
        return best;
    }
    function snapX(x, p, invert) {
        if (state.snap === !!invert) return x;
        let best = x, bd = Infinity;
        for (const s of p.positions) { const d = Math.abs(s - x); if (d < bd) { bd = d; best = s; } }
        return best;
    }
    function moveTo(i, x, y, p, invertSnap) {
        const pts = state.points, last = pts.length - 1;
        if (i === 0) x = 0;
        else if (i === last) x = 1;
        else {
            const lo = pts[i - 1][0] + MIN_GAP * 2, hi = pts[i + 1][0] - MIN_GAP * 2;
            const snapped = snapX(x, p, invertSnap);
            x = snapped > lo && snapped < hi ? snapped : clamp(x, lo, hi);
        }
        pts[i] = [x, clamp(y, 0, 1)];
    }
    function addPoint(x, y) {
        if (state.points.length >= MAX_POINTS) return -1;
        if (state.points.some((pt) => Math.abs(pt[0] - x) < MIN_GAP * 4)) return -1;
        state.points.push([x, y]);
        state.points.sort((a, b) => a[0] - b[0]);
        return state.points.findIndex((pt) => pt[0] === x && pt[1] === y);
    }
    function deletePoint(i) {
        if (i <= 0 || i >= state.points.length - 1) return false;
        pushHistory();
        state.points.splice(i, 1);
        selected = -1; hover = -1;
        commit();
        return true;
    }

    // ---- drawing ---------------------------------------------------------
    function redraw() {
        if (frame || removed) return;
        frame = requestAnimationFrame(() => { frame = 0; draw(); });
    }

    function draw() {
        const rect = canvas.getBoundingClientRect();
        W = canvas.clientWidth; H = canvas.clientHeight;
        if (!W || !H) return;
        const scale = (window.devicePixelRatio || 1) * Math.max(1, rect.width / W);
        const bw = Math.round(W * scale), bh = Math.round(H * scale);
        if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        ctx.clearRect(0, 0, W, H);

        const p = params(), r = plot();
        const f = makeEvaluator(state.points, p.mode);
        ctx.font = "10px sans-serif";

        // horizontal grid + sigma labels
        ctx.textAlign = "right"; ctx.textBaseline = "middle";
        const topPx = yToPx(1, p, r);
        const hLine = (y, strong, isTop = false) => {
            const py = Math.round(yToPx(y, p, r)) + 0.5;
            ctx.strokeStyle = strong ? "rgba(255,255,255,0.10)" : "rgba(255,255,255,0.045)";
            ctx.beginPath(); ctx.moveTo(r.x, py); ctx.lineTo(r.x + r.w, py); ctx.stroke();
            if (!strong || (!isTop && py - topPx < 12)) return;
            ctx.fillStyle = isTop ? "#b8bac0" : "#8a8b90";
            ctx.fillText(isTop ? fmt(p.sMax) : tick(y * p.sMax), r.x - 5, py);
        };
        if (state.log) {
            const lo = yRange(p) * p.sMax;
            for (let d = Math.floor(Math.log10(lo)); d <= Math.ceil(Math.log10(p.sMax)); d++) {
                for (const k of [1, 2, 5]) {
                    const v = k * 10 ** d;
                    if (v < lo * 0.999 || v > p.sMax * 1.001) continue;
                    hLine(v / p.sMax, k === 1 || (k === 5 && Math.log10(p.sMax / lo) < 2.5));
                }
            }
            hLine(1, true, true);
        } else {
            const st = niceStep(p.sMax, Math.max(2, Math.floor(r.h / 34)));
            for (let k = 0; k * st <= p.sMax * 1.0001; k++) hLine(k * st / p.sMax, true);
            hLine(1, true, true);
        }

        // vertical step grid + labels
        const n = p.positions.length;
        const spacing = r.w / Math.max(1, n - 1);
        const every = Math.max(1, Math.ceil(26 / spacing));
        ctx.textAlign = "center"; ctx.textBaseline = "top";
        for (let i = 0; i < n; i++) {
            const px = Math.round(xToPx(p.positions[i], r)) + 0.5;
            const labelled = i % every === 0 || i === n - 1;
            if (spacing > 5 || labelled) {
                ctx.strokeStyle = labelled ? "rgba(255,255,255,0.07)" : "rgba(255,255,255,0.03)";
                ctx.beginPath(); ctx.moveTo(px, r.y); ctx.lineTo(px, r.y + r.h); ctx.stroke();
            }
            if (labelled && (i === n - 1 || n - 1 - i >= every * 0.6)) {
                ctx.fillStyle = "#77787d"; ctx.fillText(String(i + 1), px, r.y + r.h + 5);
            }
        }

        // curve + fill
        const curve = new Path2D();
        for (let px = 0; px <= r.w; px++) {
            const y = yToPx(f(px / r.w), p, r);
            px ? curve.lineTo(r.x + px, y) : curve.moveTo(r.x, y);
        }
        const area = new Path2D(curve);
        area.lineTo(r.x + r.w, r.y + r.h); area.lineTo(r.x, r.y + r.h); area.closePath();
        const g = ctx.createLinearGradient(0, r.y, 0, r.y + r.h);
        g.addColorStop(0, "rgba(90,150,255,0.22)"); g.addColorStop(1, "rgba(90,150,255,0.02)");
        ctx.fillStyle = g; ctx.fill(area);
        ctx.strokeStyle = ACCENT; ctx.lineWidth = 2; ctx.stroke(curve); ctx.lineWidth = 1;

        // sampled sigmas (what the node outputs)
        const sig = sampleSigmas(state.points, p.steps, p.sMax, p.mode, p.endZero);
        ctx.fillStyle = "#e8eefc";
        for (let i = 0; i < n; i++) {
            ctx.beginPath(); ctx.arc(xToPx(p.positions[i], r), yToPx(sig[i] / p.sMax, p, r), 2.2, 0, Math.PI * 2); ctx.fill();
        }
        if (p.endZero) {
            const lx = xToPx(1, r), ly = yToPx(sig[n - 1] / p.sMax, p, r), by = r.y + r.h;
            ctx.setLineDash([3, 3]); ctx.strokeStyle = "rgba(232,238,252,0.45)";
            ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(lx, by); ctx.stroke(); ctx.setLineDash([]);
            ctx.strokeStyle = "#e8eefc"; ctx.beginPath(); ctx.arc(lx, by, 2.6, 0, Math.PI * 2); ctx.stroke();
        }

        // control points
        state.points.forEach(([x, y], i) => {
            const px = xToPx(x, r), py = yToPx(y, p, r);
            const doomed = drag?.index === i && drag.deleting;
            const isSel = i === selected;
            ctx.beginPath(); ctx.arc(px, py, isSel ? 6 : 5, 0, Math.PI * 2);
            ctx.fillStyle = doomed ? "#ff6b6b" : isSel ? ACCENT : "#fff";
            ctx.strokeStyle = doomed ? "#ff6b6b" : isSel ? "#fff" : ACCENT;
            ctx.lineWidth = 2; ctx.fill(); ctx.stroke(); ctx.lineWidth = 1;
            if (i === hover && !isSel) {
                ctx.beginPath(); ctx.arc(px, py, 9, 0, Math.PI * 2);
                ctx.strokeStyle = "rgba(255,255,255,0.4)"; ctx.stroke();
            }
        });

        // readout: dragged point, else hovered step
        let tip = null;
        if (drag && !drag.deleting) {
            const [x, y] = state.points[drag.index];
            tip = { px: xToPx(x, r), py: yToPx(y, p, r), text: `step ${round(stepOf(x, p), 2)} · σ ${fmt(y * p.sMax)}` };
        } else if (drag?.deleting) {
            const [x, y] = state.points[drag.index];
            tip = { px: xToPx(x, r), py: yToPx(y, p, r), text: "release to delete" };
        } else if (hoverPx && hoverPx[0] >= r.x - 4 && hoverPx[0] <= r.x + r.w + 4) {
            const i = clamp(Math.round(pxToX(hoverPx[0], r) * (n - 1)), 0, n - 1);
            const px = xToPx(p.positions[i], r), py = yToPx(sig[i] / p.sMax, p, r);
            ctx.strokeStyle = "rgba(255,255,255,0.18)";
            ctx.beginPath(); ctx.moveTo(Math.round(px) + 0.5, r.y); ctx.lineTo(Math.round(px) + 0.5, r.y + r.h); ctx.stroke();
            ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.strokeStyle = "#fff"; ctx.stroke();
            const next = sig[i + 1];
            tip = { px, py, text: `step ${i + 1}/${p.steps} · σ ${fmt(sig[i])}${next != null ? `  → ${fmt(next)}` : ""}` };
        }
        if (tip) {
            ctx.font = "11px sans-serif";
            const tw = ctx.measureText(tip.text).width + 12, th = 18;
            let tx = tip.px + 10, ty = tip.py - th - 8;
            if (tx + tw > W - 2) tx = tip.px - tw - 10;
            if (ty < 2) ty = tip.py + 10;
            ctx.fillStyle = "rgba(20,21,24,0.92)"; ctx.strokeStyle = "rgba(255,255,255,0.15)";
            ctx.beginPath(); ctx.roundRect ? ctx.roundRect(tx, ty, tw, th, 4) : ctx.rect(tx, ty, tw, th); ctx.fill(); ctx.stroke();
            ctx.fillStyle = "#eee"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
            ctx.fillText(tip.text, tx + 6, ty + th / 2 + 0.5);
        }

        // warning for rising sigmas
        const rises = sig.some((v, i) => i && v > sig[i - 1] + 1e-9);
        ctx.font = "10px sans-serif"; ctx.textAlign = "right"; ctx.textBaseline = "top";
        ctx.fillStyle = rises ? "#ffb86b" : "#6d6e73";
        ctx.fillText(rises ? "⚠ sigmas increase somewhere" : `${p.steps} steps · ${p.mode}`, r.x + r.w - 2, r.y + 3);

        updateFooter(p);
    }

    function updateFooter(p = params()) {
        const has = selected >= 0 && selected < state.points.length;
        editor.style.display = has ? "flex" : "none";
        if (has) {
            const [x, y] = state.points[selected];
            const endpoint = selected === 0 || selected === state.points.length - 1;
            stepIn.disabled = endpoint;
            if (document.activeElement !== stepIn) stepIn.value = String(round(stepOf(x, p), 3));
            if (document.activeElement !== sigIn) sigIn.value = String(round(y * p.sMax, 6));
            stepIn.min = "1"; stepIn.max = String(p.positions.length);
            delBtn.disabled = endpoint;
            hint.textContent = `point ${selected + 1}/${state.points.length}${endpoint ? " (end, x locked)" : ""} · arrows nudge · Del removes`;
        } else {
            hint.textContent = "click: add · drag: move · right-click / double-click / drag out: delete";
        }
        logBtn.classList.toggle("on", state.log);
        snapBtn.classList.toggle("on", state.snap);
        undoBtn.disabled = !undoStack.length; redoBtn.disabled = !redoStack.length;
    }

    // ---- pointer ---------------------------------------------------------
    canvas.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); });
    canvas.addEventListener("pointerdown", (e) => {
        e.preventDefault(); e.stopPropagation();
        root.focus({ preventScroll: true });
        const p = params(), [px, py] = localPos(e), r = plot();
        const hit = hitPoint(px, py, p);
        if (e.button === 2 || (e.button === 0 && e.altKey)) { if (hit >= 0) deletePoint(hit); return; }
        if (e.button !== 0) return;
        if (hit >= 0) {
            selected = hit;
            drag = { index: hit, pushed: false, pointer: e.pointerId, moved: false };
        } else {
            if (px < r.x - 6 || px > r.x + r.w + 6 || py < r.y - 6 || py > r.y + r.h + 6) { selected = -1; redraw(); return; }
            pushHistory();
            const x = snapX(pxToX(px, r), p, e.shiftKey);
            const i = addPoint(x, pxToY(py, p, r));
            if (i < 0) { undoStack.pop(); redraw(); return; }
            selected = i;
            drag = { index: i, pushed: true, pointer: e.pointerId, moved: true };
            commit();
        }
        canvas.setPointerCapture(e.pointerId);
        redraw();
    });
    canvas.addEventListener("pointermove", (e) => {
        const p = params(), [px, py] = localPos(e), r = plot();
        hoverPx = [px, py];
        if (!drag) {
            const h = hitPoint(px, py, p);
            canvas.style.cursor = h >= 0 ? "grab" : "crosshair";
            hover = h; redraw(); return;
        }
        e.preventDefault(); e.stopPropagation();
        if (!drag.pushed) { pushHistory(); drag.pushed = true; }
        drag.moved = true;
        const last = state.points.length - 1;
        const outside = Math.max(r.x - px, px - (r.x + r.w), r.y - py, py - (r.y + r.h));
        drag.deleting = drag.index > 0 && drag.index < last && outside > DELETE_DISTANCE;
        canvas.style.cursor = drag.deleting ? "not-allowed" : "grabbing";
        moveTo(drag.index, pxToX(px, r), pxToY(py, p, r), p, e.shiftKey);
        commit();
    });
    const endDrag = (e) => {
        if (!drag) return;
        e?.stopPropagation?.();
        const d = drag; drag = null;
        try { canvas.releasePointerCapture(d.pointer); } catch { /* already released */ }
        if (d.deleting) {
            state.points.splice(d.index, 1); selected = -1; hover = -1; commit();
        } else if (d.pushed && !d.moved) undoStack.pop();
        redraw();
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("lostpointercapture", endDrag);
    canvas.addEventListener("pointerleave", () => { hoverPx = null; if (!drag) { hover = -1; redraw(); } });
    canvas.addEventListener("dblclick", (e) => {
        e.preventDefault(); e.stopPropagation();
        const [px, py] = localPos(e);
        const hit = hitPoint(px, py, params());
        if (hit > 0 && hit < state.points.length - 1) {
            // the first click of the double-click may have created history for a no-op drag
            deletePoint(hit);
        }
    });

    // ---- keyboard --------------------------------------------------------
    root.addEventListener("keydown", (e) => {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) { e.stopPropagation(); return; }
        const mod = e.ctrlKey || e.metaKey, key = e.key;
        const p = params();
        let handled = true;
        if (mod && key.toLowerCase() === "z") e.shiftKey ? redo() : undo();
        else if (mod && key.toLowerCase() === "y") redo();
        else if ((key === "Delete" || key === "Backspace") && selected >= 0) deletePoint(selected);
        else if (key === "Escape") { selected = -1; redraw(); }
        else if (key === "Tab") {
            const k = state.points.length;
            selected = selected < 0 ? 0 : (selected + (e.shiftKey ? k - 1 : 1)) % k; redraw();
        } else if (selected >= 0 && key.startsWith("Arrow")) {
            if (!e.repeat || !undoStack.length) pushHistory();
            const [x, y] = state.points[selected];
            if (key === "ArrowUp" || key === "ArrowDown") {
                const dir = key === "ArrowUp" ? 1 : -1, big = e.shiftKey ? 10 : 1;
                let ny;
                if (state.log) ny = Math.max(y, yRange(p)) * Math.exp(dir * 0.01 * big * Math.log(1 / yRange(p)));
                else ny = y + dir * 0.005 * big;
                moveTo(selected, x, ny, p, true);
            } else {
                const dir = key === "ArrowRight" ? 1 : -1;
                const stepX = 1 / Math.max(1, p.positions.length - 1);
                const dx = state.snap && !e.altKey ? stepX : stepX / 10;
                const nx = state.snap && !e.altKey ? xOfStep(Math.round(stepOf(x, p)) + dir, p) : x + dir * dx * (e.shiftKey ? 5 : 1);
                moveTo(selected, nx, y, p, true);
            }
            commit();
        } else handled = false;
        if (handled) { e.preventDefault(); e.stopPropagation(); }
    });
    root.addEventListener("keyup", (e) => e.stopPropagation());

    // ---- footer inputs ---------------------------------------------------
    function applyInputs() {
        if (selected < 0) return;
        const p = params();
        const [x, y] = state.points[selected];
        const sv = Number(sigIn.value), st = Number(stepIn.value);
        const ny = Number.isFinite(sv) ? clamp(sv / p.sMax, 0, 1) : y;
        const nx = Number.isFinite(st) ? clamp(xOfStep(st, p), 0, 1) : x;
        if (nx === x && ny === y) return;
        pushHistory();
        moveTo(selected, nx, ny, p, true);
        commit();
    }
    for (const input of [stepIn, sigIn]) {
        input.addEventListener("change", applyInputs);
        input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { applyInputs(); input.blur(); } });
        input.addEventListener("pointerdown", (e) => e.stopPropagation());
    }
    delBtn.onclick = () => deletePoint(selected);

    // ---- toolbar ---------------------------------------------------------
    preset.addEventListener("pointerdown", (e) => e.stopPropagation());
    preset.onchange = () => {
        const def = PRESETS[preset.value];
        preset.value = "";
        if (!def) return;
        const p = params();
        pushHistory();
        const mw = widget(node, "interpolation");
        if (mw) mw.value = def.mode;
        state.points = fitCurve(def.fn, p.sMin, p.sMax, { mode: def.mode });
        selected = -1;
        commit();
    };
    logBtn.onclick = () => { state.log = !state.log; commit(); };
    snapBtn.onclick = () => { state.snap = !state.snap; commit(); };
    undoBtn.onclick = undo; redoBtn.onclick = redo;
    copyBtn.onclick = async () => {
        const p = params();
        const text = sampleSigmas(state.points, p.steps, p.sMax, p.mode, p.endZero).map((v) => round(v, 6)).join(", ");
        try { await navigator.clipboard.writeText(text); flash(copyBtn, "Copied"); }
        catch { window.prompt("Sigmas", text); }
    };
    pasteBtn.onclick = async () => {
        let text = "";
        try { text = await navigator.clipboard.readText(); } catch { /* permission denied */ }
        if (!parseList(text).length) text = window.prompt("Paste sigmas (comma or space separated)", "") || "";
        const values = parseList(text);
        if (values.length < 2) { if (text) flash(pasteBtn, "No list"); return; }
        importSigmas(values);
        flash(pasteBtn, "Pasted");
    };
    function flash(b, text) {
        const old = b.textContent; b.textContent = text;
        setTimeout(() => { b.textContent = old; }, 900);
    }
    function parseList(text) {
        return String(text || "").replace(/[\[\]()tensor]/g, " ").split(/[\s,;]+/).map(Number).filter(Number.isFinite);
    }
    function importSigmas(values) {
        const p = params();
        let vals = values.slice();
        if (p.endZero && vals.length > 2 && vals[vals.length - 1] === 0) vals.pop();
        const sMax = Math.max(...vals);
        if (!(sMax > 0)) return;
        pushHistory();
        const set = (name, v) => { const w = widget(node, name); if (w) w.value = v; };
        set("sigma_max", round(sMax, 6));
        const positive = vals.filter((v) => v > 0);
        if (positive.length) set("sigma_min", round(Math.min(...positive), 6));
        set("steps", p.endZero ? vals.length : vals.length - 1);
        const count = vals.length;
        let pts = vals.map((v, i) => [count === 1 ? 0 : i / (count - 1), v / sMax]);
        if (pts.length > MAX_POINTS) {
            const src = normalizePoints(pts), fn = makeEvaluator(src, "linear");
            pts = fitCurve((t) => fn(t) * sMax, Math.min(...positive), sMax, { mode: p.mode, maxPoints: 24, tolerance: 0.01 });
        }
        state.points = normalizePoints(pts);
        selected = -1;
        commit();
    }

    // ---- sync with widgets / external undo --------------------------------
    function sync() {
        if (removed) return;
        if (curveW.value !== lastSerialized && !drag) {
            const next = parseCurve(curveW.value || DEFAULT_CURVE);
            state = next; lastSerialized = serializeCurve(state);
            selected = Math.min(selected, state.points.length - 1);
            redraw();
        }
        const p = params();
        const rect = canvas.getBoundingClientRect();
        const sig = [p.steps, p.sMax, p.sMin, p.mode, p.endZero, Math.round((rect.width / (canvas.clientWidth || 1)) * 20)].join("|");
        if (sig !== signature) { signature = sig; redraw(); }
    }

    const ro = new ResizeObserver(() => redraw());
    ro.observe(stage);

    node._wnSigma = {
        sync,
        dispose() { removed = true; ro.disconnect(); if (frame) cancelAnimationFrame(frame); },
    };
    lastSerialized = null;
    sync();
    if (curveW.value !== serializeCurve(state)) commit();
    redraw();
}

app.registerExtension({
    name: "WepeNerd.SigmaCurve",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = created?.apply(this, arguments);
            setupSigmaCurve(this);
            if (this.size[0] < 420) this.size[0] = 420;
            if (this.size[1] < 440) this.size[1] = 440;
            return r;
        };

        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = configure?.apply(this, arguments);
            setupSigmaCurve(this);
            requestAnimationFrame(() => this._wnSigma?.sync());
            return r;
        };

        const drawFg = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function () {
            const r = drawFg?.apply(this, arguments);
            this._wnSigma?.sync();
            return r;
        };

        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            this._wnSigma?.dispose();
            return removed?.apply(this, arguments);
        };
    },
});
