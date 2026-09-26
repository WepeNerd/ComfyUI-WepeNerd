import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { executionId, imageAncestors, upstreamNodes } from "./mask_input.mjs";
import { MaskStroke, maskSettings } from "./mask_stroke.mjs";

const NODE = "WepeNerdLoadLoraMasked";
const style = document.createElement("style");
style.textContent = `.wn-mask-editor{--wn-accent:#efa5cd;font:12px var(--comfy-font-family,Arial,sans-serif);color:var(--input-text,#ddd);box-sizing:border-box;padding:0 0 8px;display:flex;flex-direction:column;gap:8px;width:100%}
.wn-mask-editor *{box-sizing:border-box}.wn-mask-editor button{font:inherit;color:var(--input-text,#ddd);background:var(--comfy-input-bg,#25262a);border:1px solid var(--border-color,#494d57);border-radius:5px;padding:6px 9px;cursor:pointer}
.wn-mask-editor button:disabled{opacity:.45;cursor:default}.wn-mask-editor button[aria-pressed=true]{outline:1px solid var(--wn-accent);background:var(--comfy-menu-bg,#35363b)}
.wn-mask-editor .wn-mask-row{display:flex;align-items:center;gap:6px;min-height:25px}.wn-mask-editor .wn-mask-body{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--border-color,#494d57);padding-top:8px}.wn-mask-editor [hidden]{display:none!important}
.wn-mask-editor .wn-mask-fold{border:0;background:transparent;padding:2px;gap:9px;text-align:left;width:100%}.wn-mask-editor .wn-mask-state{margin-left:auto;color:var(--descrip-text,#afb2bc)}
.wn-mask-editor .wn-mask-tools{gap:4px}.wn-mask-editor .wn-mask-tools button{padding:6px;display:flex;align-items:center;justify-content:center;width:30px;height:30px;flex-shrink:0}
.wn-mask-editor .wn-mask-tools svg,.wn-mask-editor .wn-mask-tools i{width:16px;height:16px;display:block}.wn-mask-editor .wn-mask-stage{position:relative;display:flex;align-items:center;justify-content:center;overflow:hidden;background:var(--comfy-input-bg,#202125);border:1px solid var(--border-color,#494d57);border-radius:3px;flex-shrink:0}
.wn-mask-editor canvas.wn-mask-canvas{display:block;touch-action:none;cursor:crosshair}.wn-mask-editor input[type=range]{min-width:40px;width:60px;flex:1;accent-color:var(--wn-accent);margin:0 4px}.wn-mask-editor .wn-mask-note{font-size:11px;color:var(--descrip-text,#afb2bc)}
.wn-mask-editor .wn-mask-between{justify-content:space-between}.wn-mask-editor .wn-mask-hint{position:absolute;inset:0;display:grid;place-items:center;pointer-events:none;color:var(--descrip-text,#afb2bc)}
.wn-mask-editor .wn-mask-decision{flex-wrap:wrap;padding:8px;background:var(--comfy-menu-bg,#35363b);border-radius:4px}
.wn-mask-editor .wn-mask-control{display:flex;align-items:center;gap:3px;flex:1;min-width:0}.wn-mask-editor .wn-mask-value{min-width:34px;text-align:right;font-variant-numeric:tabular-nums}.wn-mask-editor .wn-mask-control:has(input:disabled){opacity:.45}
.wn-mask-editor .wn-mask-canvas{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%)}.wn-mask-editor .wn-mask-composite{display:block;pointer-events:none}
.wn-mask-editor .wn-mask-override{color:var(--wn-accent);font-size:11px}.wn-mask-editor[data-external=true] .wn-mask-state{color:var(--wn-accent);font-weight:600}`;
document.head.append(style);

function element(tag, className = "") {
    const value = document.createElement(tag);
    value.className = className;
    return value;
}
function readImage(src) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error("Could not decode image; existing painting was kept."));
        image.src = src;
    });
}
function readReference(reference) {
    if (!reference) return Promise.resolve(null);
    return readImage(reference.png || api.apiURL(`/view?${new URLSearchParams({
        filename: reference.asset.name ?? reference.asset.filename,
        subfolder: reference.asset.subfolder,
        type: reference.asset.type,
    })}`));
}
export function setupMaskedLora(node, { maskOnly = false } = {}) {
    const data = node.widgets.find(w => w.name === "mask_data");
    Object.assign(data, { type: "wn_hidden", computeSize: () => [0, -4], draw() {}, mouse: () => false });
    node.properties ||= {};
    node.size[0] = Math.max(360, node.size[0]);
    const loraWidget = node.widgets.find(w => w.name === "lora_name");
    if (loraWidget) {
        loraWidget.label = "lora";
        // Only customize drawing: native combo hit handling/search and serialized values stay intact.
        loraWidget.draw = function(ctx, _node, width, y, rowHeight = 20) {
            const theme = getComputedStyle(root);
            ctx.save();
            ctx.fillStyle = theme.getPropertyValue("--comfy-input-bg").trim() || "#222";
            ctx.strokeStyle = theme.getPropertyValue("--border-color").trim() || "#666";
            ctx.beginPath(); ctx.roundRect(15, y, width - 30, rowHeight, rowHeight / 2); ctx.fill(); ctx.stroke();
            ctx.fillStyle = theme.getPropertyValue("--input-text").trim() || "#ddd";
            ctx.textBaseline = "middle"; ctx.textAlign = "left";
            ctx.fillText("lora", 37, y + rowHeight / 2);
            let name = String(this.value ?? "");
            while (name.length > 1 && ctx.measureText(name).width > width - 130) name = name.slice(0, -2) + "…";
            ctx.textAlign = "right"; ctx.fillText(name, width - 38, y + rowHeight / 2);
            for (const [x, direction] of [[25, -1], [width - 25, 1]]) {
                ctx.beginPath(); ctx.moveTo(x + direction * 4, y + rowHeight / 2);
                ctx.lineTo(x - direction * 4, y + 5); ctx.lineTo(x - direction * 4, y + rowHeight - 5); ctx.closePath(); ctx.fill();
            }
            ctx.restore();
        };
    }
    let saved = maskSettings(node.properties.wnMask, maskOnly);
    let coveredPixels = 0, maskDirty = false;
    let maskRecord = { document: { v: 1, width: 1024, height: 1024, empty: true } };
    let reference = null, busy = false, removed = false, restoring = false;
    let gesture = null, pointer = null, pending = null, upstreamArmed = false;
    let history = [];
    let frame = null, compositeDirty = true, referenceDirty = true;
    let hydrationId = 0;
    const mask = element("canvas");
    mask.width = mask.height = 1024;
    const mctx = mask.getContext("2d", { willReadFrequently: true });
    const root = element("div", "wn-mask-editor");
    root.tabIndex = 0;
    root.setAttribute("aria-label", maskOnly ? "Paint Mask editor" : "Masked LoRA editor");
    const row = element("button", "wn-mask-row wn-mask-fold");
    row.setAttribute("aria-expanded", "false");
    const thumb = element("canvas");
    thumb.width = 74; thumb.height = 50;
    thumb.style.cssText = "width:37px;height:25px;background:var(--comfy-input-bg,#202125);border:1px solid var(--border-color,#494d57);border-radius:3px;";
    const toggle = element("span");
    const state = element("span", "wn-mask-state");
    row.append(thumb, toggle, state);
    const body = element("div", "wn-mask-body");
    const toolbar = element("div", "wn-mask-row wn-mask-tools");
    toolbar.setAttribute("role", "group"); toolbar.setAttribute("aria-label", "Painting tools");
    const tool = { value: "Brush" };
    function iconButton(name, icon, paths) {
        const button = element("button"); button.title = name; button.setAttribute("aria-label", name);
        if (paths) {
            // Bundled Lucide line icons for glyphs absent from the host CSS icon set.
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            for (const [key, value] of Object.entries({viewBox:"0 0 24 24",fill:"none",stroke:"currentColor","stroke-width":"1.7","stroke-linecap":"round","stroke-linejoin":"round","aria-hidden":"true"})) svg.setAttribute(key,value);
            for (const d of paths) { const path = document.createElementNS(svg.namespaceURI,"path"); path.setAttribute("d",d); svg.append(path); }
            button.append(svg);
        } else { const glyph = element("i", `icon-[lucide--${icon}]`); glyph.setAttribute("aria-hidden","true"); button.append(glyph); }
        return button;
    }
    const brush = iconButton("Brush", null, ["m9.06 11.9 8.07-8.06a2.85 2.85 0 0 1 4.03 4.03l-8.06 8.08", "M7.07 14a3 3 0 0 0-3 3c0 1.31-1 2-2 2 1.09 1.45 2.96 2 4 2a4 4 0 0 0 4-4 3 3 0 0 0-3-3Z"]);
    const rectangle = iconButton("Rectangle", "square");
    const eraser = iconButton("Eraser", null, ["m7 21-4.3-4.3a2.4 2.4 0 0 1 0-3.4l9.6-9.6a2.4 2.4 0 0 1 3.4 0l5.6 5.6a2.4 2.4 0 0 1 0 3.4L13 21Z", "m5 11 9 9", "M22 21H7"]);
    const toolButtons = [brush, rectangle, eraser];
    const size = element("input");
    size.type = "range"; size.min = 1; size.max = 512; size.value = saved.brush;
    size.setAttribute("aria-label", "Brush size");
    const undo = iconButton("Undo", "undo-2");
    const clear = iconButton("Clear mask", "trash-2");
    const grayscale = iconButton("Grayscale mask", null, ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z", "M12 3v18M12 6h6M12 9h8M12 12h9M12 15h8M12 18h6"]);
    grayscale.title = "Inspect saved painting as grayscale coverage (black 0, white 1).";
    function control(label, input, unit) {
        const group = element("label", "wn-mask-control"), name = element("span"), value = element("span", "wn-mask-value");
        name.textContent = label;
        group.append(name, input, value);
        input.showValue = () => { value.textContent = `${input.value}${unit}`; };
        input.showValue();
        return group;
    }
    const softness = element("input"), opacity = element("input");
    for (const [input, label, value] of [[softness, "Softness", saved.softness], [opacity, "Opacity", saved.opacity]]) {
        input.type = "range"; input.min = 0; input.max = 100; input.value = value; input.setAttribute("aria-label", label);
    }
    softness.title = "Brush and eraser edge falloff; rectangles have hard edges.";
    opacity.title = "Coverage deposited or removed by one gesture; repeated gestures build coverage.";
    const brushSettings = element("div", "wn-mask-row");
    brushSettings.append(control("Softness", softness, "%"), control("Opacity", opacity, "%"));
    const source = element("button");
    source.title = maskOnly ? "Run the connected upstream path and load its first image. Never runs downstream nodes." : "Load a snapshot of the first image in the connected batch. Never runs downstream nodes.";
    toolbar.append(brush, rectangle, eraser, control("Size", size, "px"), undo, clear, grayscale);
    const override = element("div", "wn-mask-override"); override.hidden = true;
    override.textContent = "Saved painting only — disconnect MASK to apply it.";
    const sourceRow = element("div", "wn-mask-row wn-mask-between");
    const sourceState = element("span", "wn-mask-note");
    sourceRow.append(sourceState, source);
    const removeRef = element("button"); removeRef.textContent = "Remove reference";
    const decision = element("div", "wn-mask-row wn-mask-decision"); decision.hidden = true;
    const question = element("span"); question.textContent = "Replace image and clear mask?";
    const replace = element("button"); replace.textContent = "Replace";
    const cancel = element("button"); cancel.textContent = "Cancel";
    decision.append(question, replace, cancel);
    const stage = element("div", "wn-mask-stage");
    const canvas = element("canvas", "wn-mask-canvas");
    const composite = element("canvas", "wn-mask-composite");
    const tint = element("canvas"), referenceLayer = element("canvas");
    canvas.setAttribute("aria-label", "Paint mask here, or drop an image");
    const hint = element("span", "wn-mask-hint"); hint.textContent = "Drop image or paint here";
    stage.append(composite, canvas, hint);
    const dimensions = element("div", "wn-mask-note");
    dimensions.title = maskOnly ? "MASK output uses these exact dimensions. Painted areas are white (1); untouched areas are black (0)." : "The mask maps proportionally to the output image grid. A blank square mask stretches on non-square outputs. Load an image with the intended aspect ratio for aligned painting.";
    const note = element("div", "wn-mask-note"); note.setAttribute("role", "status");
    const rangeNote = element("div", "wn-mask-note"); rangeNote.setAttribute("role", "status");
    const scheduleDefaults = { start_percent: 0, end_percent: 1, apply_to: "Both" };
    const scheduleWidgets = maskOnly ? [] : node.widgets.filter(w => w.name in scheduleDefaults);
    function refreshRange() {
        const start = scheduleWidgets.find(w => w.name === "start_percent")?.value ?? 0;
        const end = scheduleWidgets.find(w => w.name === "end_percent")?.value ?? 1;
        const connected = node.inputs?.some(i => i.link != null && ["start_percent", "end_percent"].includes(i.name));
        rangeNote.textContent = !connected && start === end ? "Adapter disabled: start and end are equal."
            : !connected && start > end ? "Invalid range: start must not exceed end."
            : "Range follows the model's denoising progression; a partial-denoise run may use only part of it.";
    }
    for (const widget of scheduleWidgets) {
        const callback = widget.callback;
        widget.callback = function(...args) { callback?.apply(this, args); refreshRange(); };
    }
    const footer = element("div", "wn-mask-row wn-mask-between"); footer.append(dimensions, removeRef);
    body.append(toolbar, brushSettings, override, sourceRow, decision, stage, footer);
    if (!maskOnly) root.append(rangeNote);
    root.append(row, body, note);
    const file = element("input"); file.type = "file"; file.accept = "image/*"; file.hidden = true; root.append(file);
    const dom = node.addDOMWidget("mask_editor", "wn_mask_editor", root, { serialize: false, hideOnZoom: false });
    let height = 35;
    dom.computeSize = () => [300, height];
    dom.options.getMinHeight = () => height;
    dom.options.getMaxHeight = () => height;

    function linked() { return node.inputs?.find(i => i.name === "image")?.link != null; }
    function painted() { return coveredPixels > 0; }
    function remember() {
        const changed = Object.keys(saved).some(key => saved[key] !== node.properties.wnMask?.[key]);
        node.properties.wnMask = { ...saved };
        if (maskOnly && maskRecord?.document) writeDocument();
        if (changed) node.graph?.change();
        if (maskOnly && !restoring) app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.captureCanvasState();
    }
    function fit() {
        body.hidden = !saved.open;
        toggle.textContent = saved.open ? "Hide mask" : "Edit mask";
        row.setAttribute("aria-expanded", String(saved.open));
        const canvasHeight = Math.min(480, (node.size[0] - 24) * mask.height / mask.width);
        height = saved.open ? canvasHeight + 204 + (override.hidden ? 0 : 24) + (decision.hidden ? 0 : 78) : 35;
        if (!maskOnly) height += 40;
        if (note.textContent) height += 30;
        root.style.height = `${height}px`;
        node.setSize([Math.max(360, node.size[0]), node.computeSize()[1]]);
        node.setDirtyCanvas(true, true);
        requestRender();
    }
    function message(text) { note.textContent = text; fit(); }
    function refresh() {
        refreshRange();
        const maskLinked = node.inputs?.find(i => i.name === "mask")?.link != null;
        root.setAttribute("data-external", String(maskLinked));
        override.hidden = !maskLinked;
        state.textContent = maskLinked ? "Using MASK input" : (painted() ? "Painted" : "Empty");
        state.title = maskLinked ? "Disconnect MASK to use the saved painting." : "";
        dimensions.textContent = `${mask.width} × ${mask.height} px`;
        source.textContent = linked() ? (upstreamArmed ? "Run upstream" : "Load input") : "Open image";
        source.disabled = busy;
        removeRef.hidden = !reference;
        sourceState.textContent = reference ? (saved.reference?.source || "Reference image") : "Blank canvas";
        hint.hidden = Boolean(reference) || painted() || saved.grayscale;
        for (const button of toolButtons) button.setAttribute("aria-pressed", String(button.getAttribute("aria-label") === tool.value));
        undo.disabled = !history.length || busy;
        clear.disabled = !painted() || busy;
        size.disabled = softness.disabled = tool.value === "Rectangle";
        grayscale.setAttribute("aria-pressed", String(saved.grayscale));
        for (const input of [size, softness, opacity]) input.showValue();
        size.title = `Brush diameter: ${size.value} native pixels`;
        requestRender();
    }
    function countPixels() {
        const bytes = mctx.getImageData(0, 0, mask.width, mask.height).data;
        let count = 0;
        for (let i = 3; i < bytes.length; i += 4) if (bytes[i]) count++;
        return count;
    }
    function writeDocument() {
        const source = maskOnly ? saved.reference?.asset || "" : undefined;
        if (!maskRecord.text || maskRecord.source !== source) {
            maskRecord.text = JSON.stringify({ ...maskRecord.document, ...(maskOnly ? { source } : {}) });
            maskRecord.source = source;
        }
        data.value = maskRecord.text;
    }
    function documentChanged() {
        writeDocument(); remember(); node.graph?.change(); refresh(); requestRender(true);
    }
    function serializeMask(synchronous = false, notify = true) {
        if (!maskDirty && maskRecord?.document) {
            if (notify) documentChanged(); else writeDocument();
            return;
        }
        if (!synchronous && !maskDirty && maskRecord?.ready) return;
        saved.width = mask.width; saved.height = mask.height;
        const record = { document: null, ready: null };
        const document = { v: 1, width: mask.width, height: mask.height };
        maskRecord = record; maskDirty = false;
        const complete = png => {
            record.document = { ...document, ...(png ? { png } : { empty: true }) };
            trimHistory();
            // Old encodes still resolve for undo, but cannot overwrite newer pixels/configure.
            if (maskRecord === record && !removed) {
                if (notify) documentChanged(); else writeDocument();
            }
            return record.document;
        };
        if (!painted() || synchronous) { complete(painted() ? mask.toDataURL("image/png") : null); return; }
        record.ready = new Promise((resolve, reject) => mask.toBlob(blob => {
            if (!blob) { reject(new Error("Could not encode the mask PNG.")); return; }
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error("Could not read the mask PNG."));
            reader.readAsDataURL(blob);
        }, "image/png")).then(complete, error => {
            if (maskRecord === record && !removed) { maskDirty = true; message(error.message); }
            throw error;
        });
        // Undo can await the original rejection; avoid an unhandled background rejection.
        record.ready.catch(() => {});
    }
    function snapshot() { return { record: maskRecord, pixels: coveredPixels, reference: saved.reference }; }
    function trimHistory() {
        // Bound undo by both action count and encoded byte size.
        let total = history.reduce((n, s) => n + (s.record.document?.png?.length || 0) + (s.reference?.png?.length || 0), 0);
        while (history.length > 1 && (history.length > 20 || total > 64 * 1024 * 1024)) {
            const old = history.shift(); total -= (old.record.document?.png?.length || 0) + (old.reference?.png?.length || 0);
        }
    }
    function push(value) { history.push(value); trimHistory(); }
    async function restore(value) {
        const revision = hydrationId;
        busy = true; refresh();
        try {
            const document = value.record.document || await value.record.ready;
            const [bitmap, ref] = await Promise.all([document.png ? readImage(document.png) : null, readReference(value.reference)]);
            if (removed || revision !== hydrationId) return;
            mask.width = document.width; mask.height = document.height;
            if (bitmap) mctx.drawImage(bitmap, 0, 0);
            coveredPixels = value.pixels; maskDirty = false; maskRecord = value.record;
            reference = ref; saved.reference = value.reference; referenceDirty = true;
            if (history.at(-1) === value) history.pop();
            note.textContent = "";
            saved.width = mask.width; saved.height = mask.height; documentChanged();
        } catch (error) {
            if (!removed && revision === hydrationId) message(`Cannot undo: ${error.message}`);
        } finally { busy = false; refresh(); fit(); }
    }
    function requestRender(content = false) {
        compositeDirty ||= content;
        if (removed || frame !== null) return;
        frame = requestAnimationFrame(() => { frame = null; if (!removed) render(); });
    }
    function writeTile(x, y, w, h, bytes) { mctx.putImageData(new ImageData(bytes, w, h), x, y); maskDirty = true; }
    function flushGesture() {
        if (!gesture || gesture.tool === "Rectangle") return;
        if (gesture.samples.length) {
            gesture.stroke.polyline([gesture.last, ...gesture.samples]);
            gesture.last = gesture.samples.at(-1);
        }
        gesture.samples.length = 0;
        if (gesture.stroke.flush(writeTile)) compositeDirty = true;
        coveredPixels = gesture.before.pixels + gesture.stroke.pixelDelta;
    }
    function render() {
        flushGesture();
        hint.hidden = Boolean(reference) || painted() || Boolean(gesture?.stroke.changed) || saved.grayscale;
        const available = Math.max(100, node.size[0] - 24);
        const scale = Math.min(available / mask.width, 480 / mask.height);
        const width = Math.round(mask.width * scale), h = Math.round(mask.height * scale);
        const dpr = window.devicePixelRatio || 1;
        const pixelWidth = Math.max(1, Math.round(width * dpr)), pixelHeight = Math.max(1, Math.round(h * dpr));
        for (const surface of [canvas, composite, tint, referenceLayer]) {
            if (surface.width !== pixelWidth) { surface.width = pixelWidth; compositeDirty = referenceDirty = true; }
            if (surface.height !== pixelHeight) { surface.height = pixelHeight; compositeDirty = referenceDirty = true; }
        }
        canvas.style.width = `${width}px`; canvas.style.height = `${h}px`;
        composite.style.width = `${width}px`; composite.style.height = `${h}px`;
        if (referenceDirty) {
            const rctx = referenceLayer.getContext("2d"); rctx.clearRect(0, 0, pixelWidth, pixelHeight);
            if (reference) rctx.drawImage(reference, 0, 0, pixelWidth, pixelHeight);
            referenceDirty = false;
        }
        if (compositeDirty) {
            const ctx = composite.getContext("2d"), tctx = tint.getContext("2d");
            ctx.clearRect(0, 0, pixelWidth, pixelHeight);
            if (saved.grayscale) { ctx.fillStyle = "black"; ctx.fillRect(0, 0, pixelWidth, pixelHeight); }
            else ctx.drawImage(referenceLayer, 0, 0);
            tctx.clearRect(0, 0, pixelWidth, pixelHeight);
            tctx.globalCompositeOperation = "source-over"; tctx.drawImage(mask, 0, 0, pixelWidth, pixelHeight);
            tctx.globalCompositeOperation = "source-in"; tctx.fillStyle = saved.grayscale ? "white" : "#d84d9d"; tctx.fillRect(0, 0, pixelWidth, pixelHeight);
            tctx.globalCompositeOperation = "source-over";
            ctx.globalAlpha = saved.grayscale ? 1 : .45; ctx.drawImage(tint, 0, 0); ctx.globalAlpha = 1;
            const tc = thumb.getContext("2d"), ts = Math.min(74 / mask.width, 50 / mask.height);
            tc.clearRect(0, 0, 74, 50);
            tc.drawImage(composite, (74 - mask.width * ts) / 2, (50 - mask.height * ts) / 2, mask.width * ts, mask.height * ts);
            compositeDirty = false;
        }
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, pixelWidth, pixelHeight);
        if (!saved.open) return;
        if (gesture?.tool === "Rectangle") {
            ctx.strokeStyle = saved.grayscale ? "white" : "#d84d9d"; ctx.lineWidth = dpr;
            ctx.strokeRect(gesture.start.x / mask.width * canvas.width, gesture.start.y / mask.height * canvas.height,
                (gesture.last.x - gesture.start.x) / mask.width * canvas.width, (gesture.last.y - gesture.start.y) / mask.height * canvas.height);
        }
        if (pointer && tool.value !== "Rectangle") {
            ctx.beginPath(); ctx.arc(pointer.x / mask.width * canvas.width, pointer.y / mask.height * canvas.height, Number(size.value) / mask.width * canvas.width / 2, 0, Math.PI * 2);
            ctx.strokeStyle = "#000"; ctx.lineWidth = 3 * dpr; ctx.stroke();
            ctx.strokeStyle = "#fff"; ctx.lineWidth = dpr; ctx.stroke();
        }
    }
    function coords(event) {
        const rect = canvas.getBoundingClientRect();
        return { x: Math.max(0, Math.min(mask.width, (event.clientX - rect.left) / rect.width * mask.width)), y: Math.max(0, Math.min(mask.height, (event.clientY - rect.top) / rect.height * mask.height)) };
    }
    function finish(commit, notify = true) {
        if (!gesture) return;
        if (commit) flushGesture();
        const action = gesture; gesture = null;
        if (canvas.hasPointerCapture(action.id)) canvas.releasePointerCapture(action.id);
        if (!commit) {
            action.stroke.cancel(writeTile); coveredPixels = action.before.pixels;
            maskRecord = action.before.record; maskDirty = false;
            if (notify && maskRecord.document) documentChanged();
            refresh(); requestRender(true); return;
        }
        if (action.tool === "Rectangle") {
            action.stroke.rectangle(action.start, action.last); action.stroke.flush(writeTile);
            coveredPixels = action.before.pixels + action.stroke.pixelDelta;
        }
        if (action.stroke.changed) { push(action.before); serializeMask(); }
        refresh(); requestRender(true);
    }
    canvas.onpointerdown = event => {
        if (gesture || busy || restoring || pending || event.button !== 0 || Number(opacity.value) === 0) return;
        root.focus(); event.preventDefault(); event.stopPropagation();
        const point = coords(event);
        gesture = { id: event.pointerId, tool: tool.value, start: point, last: point, before: snapshot(), samples: [point],
            stroke: new MaskStroke(mask.width, mask.height, (x, y, w, h) => mctx.getImageData(x, y, w, h).data,
                { size: Number(size.value), softness: Number(softness.value), opacity: Number(opacity.value), erase: tool.value === "Eraser" }) };
        canvas.setPointerCapture(event.pointerId);
        pointer = point; requestRender();
    };
    canvas.onpointermove = event => {
        if (!saved.open) return;
        pointer = coords(event);
        if (gesture && gesture.id === event.pointerId) {
            if (gesture.tool === "Rectangle") gesture.last = pointer;
            else {
                const samples = event.getCoalescedEvents?.() || [];
                for (const sample of samples) gesture.samples.push(coords(sample));
                gesture.samples.push(pointer);
            }
        }
        requestRender();
    };
    canvas.onpointerup = event => { if (gesture?.id === event.pointerId) { canvas.onpointermove(event); finish(true); } };
    canvas.onpointercancel = () => void finish(false);
    canvas.onlostpointercapture = () => void finish(false);
    canvas.onpointerleave = () => { if (!gesture) { pointer = null; requestRender(); } };
    const blur = () => { pointer = null; finish(false); requestRender(); };
    window.addEventListener("blur", blur);
    root.onkeydown = event => {
        if (event.key === "Escape") { event.stopPropagation(); void finish(false); }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.stopPropagation(); undo.click(); }
    };
    for (const name of ["pointerdown", "pointermove", "pointerup", "wheel", "dblclick"]) root.addEventListener(name, e => e.stopPropagation());
    row.onclick = async () => { await finish(false); saved.open = !saved.open; remember(); fit(); };
    for (const [input, key] of [[size, "brush"], [softness, "softness"], [opacity, "opacity"]]) {
        input.oninput = () => { finish(false); saved[key] = Number(input.value); remember(); refresh(); };
    }
    grayscale.onclick = () => { saved.grayscale = !saved.grayscale; remember(); refresh(); requestRender(true); };
    for (const button of toolButtons) button.onclick = async () => { await finish(false); tool.value = button.getAttribute("aria-label"); refresh(); };
    undo.onclick = async () => { if (gesture || busy || restoring || !history.length) return; await restore(history.at(-1)); };
    clear.onclick = () => { if (busy || gesture) return; push(snapshot()); mctx.clearRect(0, 0, mask.width, mask.height); coveredPixels = 0; maskDirty = true; serializeMask(); };
    removeRef.onclick = () => { if (busy || gesture) return; push(snapshot()); reference = null; saved.reference = null; referenceDirty = true; remember(); refresh(); requestRender(true); fit(); };
    const oldMenu = node.getExtraMenuOptions;
    node.getExtraMenuOptions = function(...args) { oldMenu?.apply(this, args); args[1].push({content:"Open reference image…",callback:() => file.click()}); };

    async function adopt(image, asset) {
        if (removed) return;
        await finish(false);
        const apply = () => {
            push(snapshot());
            if (image.naturalWidth !== mask.width || image.naturalHeight !== mask.height) {
                mask.width = image.naturalWidth; mask.height = image.naturalHeight; coveredPixels = 0; maskDirty = true;
            }
            reference = image; saved.reference = asset; referenceDirty = true; saved.open = true; note.textContent = "";
            serializeMask(); remember(); refresh(); requestRender(true); fit();
        };
        if (painted() && (image.naturalWidth !== mask.width || image.naturalHeight !== mask.height)) {
            pending = apply; saved.open = true; decision.hidden = false; fit();
        } else apply();
    }
    replace.onclick = () => { const action = pending; pending = null; decision.hidden = true; action?.(); };
    cancel.onclick = () => { pending = null; decision.hidden = true; fit(); };
    async function uploadReference(blob) {
        const form = new FormData(); form.append("image", blob, `mask-reference-${crypto.randomUUID()}.png`); form.append("subfolder", maskOnly ? "wepenerd_paint_mask" : "wepenerd_masked_lora"); form.append("type", "input");
        const response = await api.fetchApi("/upload/image", { method: "POST", body: form });
        if (!response.ok) throw new Error("Could not save reference image; existing painting was kept.");
        return response.json();
    }
    async function importImage(blob, sourceName = "Dropped / opened image") {
        if (busy || pending) return;
        busy = true; refresh();
        try {
            // Browser decoding applies EXIF orientation before the exact-size PNG snapshot.
            const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
            const raster = element("canvas"); raster.width = bitmap.width; raster.height = bitmap.height;
            raster.getContext("2d").drawImage(bitmap, 0, 0); bitmap.close();
            const upload = await new Promise(resolve => raster.toBlob(resolve, "image/png"));
            const asset = await uploadReference(upload);
            const reference = { asset, source: sourceName };
            await adopt(await readReference(reference), reference);
        } catch (error) { message(error.message); }
        finally { busy = false; refresh(); }
    }
    file.onchange = () => { if (file.files[0]) void importImage(file.files[0]); file.value = ""; };
    function dropped(event) {
        const item = [...(event.dataTransfer?.files || [])][0];
        if (!item) return false;
        event.preventDefault(); event.stopPropagation(); void importImage(item); return true;
    }
    root.ondragover = e => { e.preventDefault(); e.stopPropagation(); };
    root.ondrop = dropped;
    const oldDrop = node.onDragDrop;
    node.onDragDrop = function(event) { return dropped(event) || oldDrop?.call(this, event); };
    node.onDragOver = () => true;

    async function loadInput() {
        if (busy || pending || restoring) return;
        if (!linked()) { file.click(); return; }
        const link = node.graph.links[node.inputs.find(i => i.name === "image").link];
        const cached = node.graph === (app.rootGraph || app.graph) ? app.nodeOutputs?.[link?.origin_id] : null;
        if (!maskOnly && cached?.images?.[0] && !upstreamArmed) {
            const response = await api.fetchApi(`/view?${new URLSearchParams(cached.images[0])}`);
            if (response.ok) { await importImage(await response.blob(), "Input image"); return; }
        }
        if (!maskOnly && !upstreamArmed) { upstreamArmed = true; message("Run upstream to load image (first image of batch)."); refresh(); return; }
        busy = true; refresh();
        let { output: full } = await app.graphToPrompt();
        const id = executionId(node, app.rootGraph || app.graph);
        let { input, output: subset } = imageAncestors(full, id);
        const ancestors = maskOnly ? upstreamNodes(app.rootGraph || app.graph, subset) : [];
        for (const ancestor of ancestors) for (const widget of ancestor.widgets || []) await widget.beforeQueued?.({ isPartialExecution: true });
        if (ancestors.length) {
            ({ output: full } = await app.graphToPrompt());
            ({ input, output: subset } = imageAncestors(full, id));
        }
        const sink = `wn_mask_snapshot_${crypto.randomUUID()}`;
        subset[sink] = { class_type: "WN_MaskedLoraSnapshot", inputs: { image: input } };
        busy = true; refresh();
        let promptId;
        const done = async event => {
            if (String(event.detail.node) !== sink || removed) return;
            cleanup(); busy = false;
            try {
                const reference = { asset: event.detail.output.images[0], source: "Input image" };
                await adopt(await readReference(reference), reference);
            } catch (error) { message(error.message); }
            upstreamArmed = false; refresh();
        };
        const failed = event => {
            if (event.detail.prompt_id !== promptId) return;
            cleanup(); busy = false; message("Upstream image run failed or was interrupted; existing painting was kept."); refresh();
        };
        const cleanup = () => { api.removeEventListener("executed", done); api.removeEventListener("execution_error", failed); api.removeEventListener("execution_interrupted", failed); pendingCleanup = null; };
        pendingCleanup = cleanup;
        api.addEventListener("executed", done); api.addEventListener("execution_error", failed); api.addEventListener("execution_interrupted", failed);
        try {
            message("Loading upstream image…");
            const queued = await api.queuePrompt(0, { output: subset, workflow: { nodes: [], links: [] } });
            promptId = queued.prompt_id;
            for (const ancestor of ancestors) for (const widget of ancestor.widgets || []) widget.afterQueued?.({ isPartialExecution: true });
        } catch (error) { cleanup(); busy = false; refresh(); throw error; }
    }
    let pendingCleanup = null;
    source.onclick = () => loadInput().catch(error => { busy = false; refresh(); message(error.message); });
    if (maskOnly) {
        const oldExecuted = node.onExecuted;
        node.onExecuted = function(output) {
            oldExecuted?.apply(this, arguments);
            const asset = output?.paint_mask_source?.[0];
            if (!asset || removed || !linked() || busy || pending || gesture) return;
            if (saved.reference?.asset?.filename === asset.filename) return;
            void readReference({ asset }).then(image => {
                if (removed || !linked() || busy || pending || gesture) return;
                return adopt(image, { asset, source: "Input image" });
            }).catch(error => message(error.message));
        };
    }
    const oldConnections = node.onConnectionsChange;
    node.onConnectionsChange = function(...args) { oldConnections?.apply(this, args); upstreamArmed = false; refresh(); fit(); };
    const oldResize = node.onResize;
    let previousWidth = node.size[0];
    node.onResize = function(...args) {
        oldResize?.apply(this, args);
        if (previousWidth !== node.size[0]) { previousWidth = node.size[0]; queueMicrotask(fit); }
        requestRender();
    };
    const displayResize = () => requestRender();
    window.addEventListener("resize", displayResize);
    const oldSerializeValue = data.serializeValue;
    function checkpoint() { if (restoring) return; flushGesture(); serializeMask(true, false); }
    data.serializeValue = function(...args) { checkpoint(); return oldSerializeValue ? oldSerializeValue.apply(this, args) : this.value; };
    const oldSerialize = node.onSerialize;
    node.onSerialize = function(info) {
        checkpoint();
        oldSerialize?.apply(this, arguments);
        if (info.widgets_values) info.widgets_values[node.widgets.indexOf(data)] = data.value;
        if (info.properties) info.properties.wnMask = maskSettings(saved, maskOnly);
    };
    const oldRemoved = node.onRemoved;
    node.onRemoved = function(...args) {
        finish(false, false); removed = true; hydrationId++; maskRecord = null;
        if (frame !== null) cancelAnimationFrame(frame);
        frame = null; pending = null; pendingCleanup?.();
        window.removeEventListener("blur", blur); window.removeEventListener("resize", displayResize);
        root.remove(); oldRemoved?.apply(this, args);
    };
    const oldConfigure = node.onConfigure;
    node.onConfigure = function(info) {
        finish(false, false);
        oldConfigure?.apply(this, arguments);
        // Older graphs end with the nonserialized editor's empty widget slot.
        for (const widget of scheduleWidgets) {
            const value = info?.widgets_values?.[node.widgets.indexOf(widget)];
            widget.value = value == null || value === "" ? scheduleDefaults[widget.name] : value;
        }
        refreshRange(); void hydrate();
    };
    async function hydrate() {
        const revision = ++hydrationId;
        restoring = true;
        try {
            const settings = maskSettings(node.properties.wnMask, maskOnly);
            const maskData = data.value ? JSON.parse(data.value) : null;
            maskRecord = null; maskDirty = false;
            if (maskOnly && !settings.reference && maskData?.source) settings.reference = typeof maskData.source === "string"
                ? { png: maskData.source, source: "Saved image" } : { asset: maskData.source, source: "Saved image" };
            const bitmap = maskData?.png ? await readImage(maskData.png) : null;
            if (removed || revision !== hydrationId) return;
            let ref = await readReference(settings.reference);
            if (removed || revision !== hydrationId) return;
            if (settings.reference?.png) {
                const { png, ...stored } = settings.reference;
                // Drop embedded bytes only after verifying or restoring the saved asset.
                try {
                    if (!stored.asset) throw new Error("No saved asset");
                    ref = await readReference(stored);
                } catch {
                    const response = await fetch(png);
                    stored.asset = await uploadReference(await response.blob());
                    ref = await readReference(stored);
                }
                settings.reference = stored;
            }
            if (removed || revision !== hydrationId) return;
            saved = settings;
            size.value = saved.brush; softness.value = saved.softness; opacity.value = saved.opacity;
            mask.width = maskData?.width || saved.width || 1024; mask.height = maskData?.height || saved.height || 1024;
            if (bitmap) mctx.drawImage(bitmap, 0, 0);
            coveredPixels = bitmap ? countPixels() : 0;
            maskRecord = { document: maskData || { v: 1, width: mask.width, height: mask.height, empty: true } };
            reference = ref; referenceDirty = true;
            if (maskOnly) writeDocument();
            history = []; remember(); refresh(); requestRender(true); fit();
            if (maskOnly) { restoring = false; remember(); }
        } catch (error) { if (!removed && revision === hydrationId) message(`Cannot restore mask: ${error.message}`); }
        finally { if (revision === hydrationId) restoring = false; }
    }
    void hydrate();
}

app.registerExtension({
    name: "wepenerd.masked_lora",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE && nodeData.name !== "WN_PaintMask") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function(...args) { created?.apply(this, args); setupMaskedLora(this, { maskOnly: nodeData.name === "WN_PaintMask" }); };
    },
});
