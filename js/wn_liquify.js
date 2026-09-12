import { app } from "../../scripts/app.js";
import { LiquifyWarp, PREVIEW_EDGE, MAX_POINTS, validateStrokes } from "./liquify_warp.mjs";

function button(text, title) {
    const element = document.createElement("button");
    element.textContent = text; element.title = title || text;
    element.style.cssText = "background:#353535;color:#ddd;border:1px solid #555;border-radius:4px;padding:5px 8px;cursor:pointer;font:inherit;";
    return element;
}

function slider(name, min, max, value, step) {
    const label = document.createElement("label"), input = document.createElement("input"), readout = document.createElement("span");
    label.style.cssText = "flex:1;min-width:0;font-size:11px;color:#bbb;";
    input.type = "range"; input.min = min; input.max = max; input.step = step; input.value = value;
    input.style.cssText = "display:block;width:100%;margin:3px 0 0;";
    const update = () => { readout.textContent = `${name} ${input.value}`; };
    input.addEventListener("input", update); update(); label.append(readout, input);
    return { label, input, update };
}

function setupLiquify(node) {
    if (node.__liquify) return;
    const widget = node.widgets?.find(w => w.name === "image_data");
    widget.hidden = true; widget.computeSize = () => [0, -4]; widget.type = "hidden_liquify_data";
    const root = document.createElement("div");
    root.className = "wn-liquify"; root.tabIndex = 0;
    root.style.cssText = "display:flex;flex-direction:column;gap:6px;width:100%;height:100%;min-height:0;box-sizing:border-box;padding:4px;font:11px sans-serif;outline:none;";
    const toolbar = document.createElement("div"); toolbar.style.cssText = "display:flex;gap:4px;";
    const load = button("Load"), undo = button("Undo", "Undo stroke (Ctrl/Cmd+Z)"), redo = button("Redo", "Redo stroke (Ctrl/Cmd+Shift+Z)");
    const reset = button("Reset", "Remove all warps; Undo restores them"), compare = button("Original", "Hold to compare with the original");
    toolbar.append(load, undo, redo, reset, compare);
    const controls = document.createElement("div"); controls.style.cssText = "display:flex;gap:10px;";
    const size = slider("Size", 8, 600, 80, 1), strength = slider("Strength", 0.05, 1, 0.5, 0.05);
    controls.append(size.label, strength.label);
    const stage = document.createElement("div");
    stage.style.cssText = "position:relative;flex:1;min-height:100px;display:flex;align-items:center;justify-content:center;background:#222;border:1px solid #444;border-radius:4px;overflow:hidden;";
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "display:block;max-width:100%;max-height:100%;object-fit:contain;touch-action:none;cursor:crosshair;";
    canvas.width = canvas.height = 0;
    const cursor = document.createElement("div");
    cursor.style.cssText = "position:absolute;display:none;border:1px solid white;box-shadow:0 0 0 1px #0008;border-radius:50%;pointer-events:none;transform:translate(-50%,-50%);box-sizing:border-box;";
    const hint = document.createElement("div"); hint.textContent = "Load an image, or connect IMAGE and Queue once";
    hint.style.cssText = "position:absolute;color:#999;text-align:center;padding:8px;pointer-events:none;";
    const status = document.createElement("div"); status.style.cssText = "min-height:13px;color:#aaa;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
    const picker = document.createElement("input"); picker.type = "file"; picker.accept = "image/png,image/jpeg,image/webp"; picker.hidden = true;
    stage.append(canvas, cursor, hint); root.append(toolbar, controls, stage, status, picker);
    node.addDOMWidget("liquify_ui", "liquify", root, { serialize: false, hideOnZoom: false });
    const context = canvas.getContext("2d", { willReadFrequently: true });
    let doc = { v: 2, source: "", strokes: [], redo: [], source_mode: "file" };
    let sourceJSON = '""', previewJSON = '""', model = null, pixels = null;
    let removed = false, loading = false, loadVersion = 0, pendingLoad = Promise.resolve();
    let active = null, pointer = null, frame = 0, renderedPoints = 1, hover = null, dirty = true, comparing = false;
    let resetHistory = null, pointCount = 0, previousRedo = null, restoreError = null;

    function inputConnected() { return node.inputs?.some(input => input.name === "image" && input.link != null); }
    function info(message, error = false) {
        status.textContent = message; status.title = message; status.style.color = error ? "#ffb4a9" : "#aaa";
    }
    function updateControls() {
        undo.disabled = loading || (!doc.strokes.length && !resetHistory);
        redo.disabled = loading || !doc.redo.length;
        reset.disabled = loading || !doc.strokes.length;
        compare.disabled = loading || !model;
        load.disabled = inputConnected();
        load.title = load.disabled ? "Disconnect IMAGE to load a file" : "Load PNG, JPEG or WebP";
        for (const control of [load, undo, redo, reset, compare]) {
            control.style.opacity = control.disabled ? "0.45" : "1";
            control.style.cursor = control.disabled ? "default" : "pointer";
        }
    }
    function recount() {
        pointCount = [...doc.strokes, ...doc.redo].reduce((sum, stroke) => sum + stroke.points.length, 0);
    }
    function dimensions() {
        if (!model) return;
        const batch = doc.batch > 1 ? ` · same warp on ${doc.batch} images` : "";
        info(`${doc.width} × ${doc.height} output${batch} · ${doc.strokes.length} strokes`);
    }
    function serialize() {
        if (restoreError || removed || loading) return widget.value;
        if (!dirty) return widget.value;
        const meta = JSON.stringify({ v: 2, source_mode: doc.source_mode, width: doc.width, height: doc.height,
            batch: doc.batch, strokes: doc.strokes, redo: doc.redo, brush: [+size.input.value, +strength.input.value] });
        widget.value = `${meta.slice(0, -1)},"source":${sourceJSON},"preview":${previewJSON}}`;
        dirty = false; return widget.value;
    }
    function changed(capture = true) {
        dirty = true; serialize(); updateControls(); node.setDirtyCanvas?.(true, true);
        if (capture && node.graph === (app.rootGraph || app.graph)) {
            app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.captureCanvasState();
        }
    }
    function display() {
        if (!model) return;
        pixels.data.set(comparing ? model.source : model.output); context.putImageData(pixels, 0, 0);
    }
    function flush() {
        if (frame) cancelAnimationFrame(frame); frame = 0;
        if (active && model && active.points.length > renderedPoints) {
            const region = model.apply(active, renderedPoints); renderedPoints = active.points.length;
            model.render(region);
            if (region[2] > region[0] && region[3] > region[1]) {
                pixels.data.set(model.output);
                context.putImageData(pixels, 0, 0, region[0], region[1], region[2] - region[0], region[3] - region[1]);
            }
        }
        if (hover && model) {
            const rect = canvas.getBoundingClientRect(), bounds = stage.getBoundingClientRect();
            const scale = stage.offsetWidth / bounds.width;
            const diameter = Math.min(+size.input.value, 2 * Math.max(model.width, model.height)) / model.width * rect.width * scale;
            Object.assign(cursor.style, { display: "block", width: `${diameter}px`, height: `${diameter}px`,
                left: `${(hover[0] - bounds.left) * scale - stage.clientLeft}px`,
                top: `${(hover[1] - bounds.top) * scale - stage.clientTop}px` });
        } else cursor.style.display = "none";
    }
    function schedule() { if (!frame) frame = requestAnimationFrame(flush); }
    function replay() { if (model) { model.replay(doc.strokes); display(); } }
    function finish(event) {
        if (!active || (event?.pointerId != null && event.pointerId !== pointer)) return;
        flush();
        if (active.points.length < 2) { doc.strokes.pop(); doc.redo = previousRedo || []; recount(); }
        previousRedo = null;
        active = null;
        if (canvas.hasPointerCapture(pointer)) canvas.releasePointerCapture(pointer);
        pointer = null; window.removeEventListener("blur", finish);
        dimensions(); changed();
    }
    function undoStroke() {
        if (loading || removed) return;
        finish();
        if (resetHistory && !doc.strokes.length) { doc.strokes = resetHistory; resetHistory = null; }
        else if (doc.strokes.length) { doc.redo.push(doc.strokes.pop()); }
        recount(); replay(); dimensions(); changed();
    }
    function redoStroke() {
        if (loading || removed) return;
        finish(); if (!doc.redo.length) return;
        doc.strokes.push(doc.redo.pop()); replay(); dimensions(); changed();
    }
    async function decode(url) {
        if (!/^data:image\/(png|jpeg|webp);base64,/i.test(url)) throw Error("Load a PNG, JPEG or WebP image.");
        const image = new Image(); image.src = url; await image.decode();
        if (image.naturalWidth * image.naturalHeight > 64 * 1024 * 1024) throw Error("Image exceeds 64 megapixels.");
        return image;
    }
    function setPreview(image) {
        const scale = Math.min(1, PREVIEW_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        context.clearRect(0, 0, canvas.width, canvas.height); context.drawImage(image, 0, 0, canvas.width, canvas.height);
        pixels = context.getImageData(0, 0, canvas.width, canvas.height);
        model = new LiquifyWarp(canvas.width, canvas.height, pixels.data);
        hint.style.display = "none"; replay(); dimensions();
    }
    function loadFile(file) {
        if (!file || !/^image\/(png|jpeg|webp)$/.test(file.type)) return false;
        if (inputConnected()) { info("Disconnect IMAGE before loading a file.", true); return true; }
        if (file.size > 64 * 1024 * 1024) { info("Image exceeds 64 MiB.", true); return true; }
        finish(); const version = ++loadVersion; loading = true; updateControls(); info("Loading image…");
        pendingLoad = (async () => {
            const data = await new Promise((resolve, reject) => {
                const reader = new FileReader(); reader.onload = () => resolve(reader.result);
                reader.onerror = () => reject(Error("Could not read image.")); reader.readAsDataURL(file);
            });
            const image = await decode(data);
            if (removed || version !== loadVersion) return;
            doc = { v: 2, source: data, source_mode: "file", width: image.naturalWidth, height: image.naturalHeight, strokes: [], redo: [] };
            restoreError = null;
            sourceJSON = JSON.stringify(data); previewJSON = '""'; pointCount = 0; resetHistory = null;
            setPreview(image); loading = false; changed();
        })().catch(error => { if (!removed && version === loadVersion) info(error.message, true); })
            .finally(() => { if (!removed && version === loadVersion) { loading = false; updateControls(); } });
        return true;
    }
    async function restore() {
        const value = widget.value, version = ++loadVersion;
        active = null;
        if (pointer !== null && canvas.hasPointerCapture(pointer)) canvas.releasePointerCapture(pointer);
        pointer = null; window.removeEventListener("blur", finish);
        loading = true; updateControls();
        try {
            const restored = value?.trim().startsWith("{") ? JSON.parse(value)
                : { v: 2, source: value || "", source_mode: "file", strokes: [], redo: [] };
            if (restored.v !== 2 || !Array.isArray(restored.strokes)) throw Error("Unsupported Liquify document.");
            validateStrokes([...restored.strokes, ...(restored.redo || [])]);
            const source = restored.source_mode === "input" ? restored.preview : restored.source;
            const image = source ? await decode(source) : null;
            if (removed || version !== loadVersion) return;
            doc = { ...restored, redo: restored.redo || [] };
            restoreError = null;
            sourceJSON = JSON.stringify(doc.source || ""); previewJSON = JSON.stringify(doc.preview || "");
            pointCount = [...doc.strokes, ...doc.redo].reduce((sum, stroke) => sum + stroke.points.length, 0);
            resetHistory = null;
            if (doc.brush) { size.input.value = doc.brush[0]; strength.input.value = doc.brush[1]; size.update(); strength.update(); }
            if (image) { doc.width ||= image.naturalWidth; doc.height ||= image.naturalHeight; setPreview(image); }
            else { model = pixels = null; canvas.width = canvas.height = 0; hint.style.display = "block"; }
            dirty = true;
        } catch (error) { if (!removed && version === loadVersion) { restoreError = error; info(error.message, true); } }
        finally { if (!removed && version === loadVersion) { loading = false; updateControls(); } }
    }
    function point(event) {
        const rect = canvas.getBoundingClientRect();
        return [Math.max(-1, Math.min(2, (event.clientX - rect.left) / rect.width)),
            Math.max(-1, Math.min(2, (event.clientY - rect.top) / rect.height))];
    }
    canvas.addEventListener("pointerdown", event => {
        if (!model || loading || pointer !== null || event.button !== 0) return;
        event.preventDefault(); event.stopPropagation(); root.focus({ preventScroll: true });
        if (doc.strokes.length >= 2000 || pointCount >= MAX_POINTS) { info("Stroke history is full. Continue editing in another Liquify node.", true); return; }
        previousRedo = doc.redo;
        pointCount -= doc.redo.reduce((sum, stroke) => sum + stroke.points.length, 0);
        doc.redo = []; resetHistory = null;
        active = { radius: Math.min(1, +size.input.value / (2 * Math.max(model.width, model.height))), strength: +strength.input.value, points: [point(event)] };
        doc.strokes.push(active); pointCount++; renderedPoints = 1; dirty = true;
        pointer = event.pointerId; canvas.setPointerCapture(pointer); window.addEventListener("blur", finish);
        hover = [event.clientX, event.clientY]; schedule();
    });
    canvas.addEventListener("pointermove", event => {
        if (!model || loading) return;
        hover = [event.clientX, event.clientY]; schedule();
        if (!active || event.pointerId !== pointer) return;
        event.preventDefault(); event.stopPropagation();
        for (const sample of event.getCoalescedEvents?.().length ? event.getCoalescedEvents() : [event]) {
            const next = point(sample), last = active.points.at(-1);
            if (next[0] === last[0] && next[1] === last[1]) continue;
            if (pointCount >= MAX_POINTS) { finish(); info("Stroke history is full. Continue in another Liquify node.", true); break; }
            active.points.push(next); pointCount++; dirty = true;
        }
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) canvas.addEventListener(name, finish);
    canvas.addEventListener("pointerleave", () => { hover = null; schedule(); });
    load.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => { loadFile(picker.files?.[0]); picker.value = ""; });
    undo.addEventListener("click", undoStroke); redo.addEventListener("click", redoStroke);
    reset.addEventListener("click", () => {
        finish(); resetHistory = doc.strokes; doc.strokes = []; doc.redo = []; recount();
        replay(); dimensions(); changed();
    });
    function endCompare() {
        comparing = false; display(); window.removeEventListener("blur", endCompare);
    }
    compare.addEventListener("pointerdown", event => {
        finish(); comparing = true; compare.setPointerCapture(event.pointerId); display();
        window.addEventListener("blur", endCompare);
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) compare.addEventListener(name, endCompare);
    for (const control of [size, strength]) control.input.addEventListener("change", () => changed());
    root.addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase())) {
            event.preventDefault(); event.stopPropagation();
            if (event.shiftKey || event.key.toLowerCase() === "y") redoStroke(); else undoStroke();
        }
    });
    root.addEventListener("dragover", event => { event.preventDefault(); event.stopPropagation(); });
    root.addEventListener("drop", event => {
        event.preventDefault(); event.stopPropagation(); loadFile([...event.dataTransfer.files].find(file => file.type.startsWith("image/")));
    });
    const dropFile = node.onDropFile;
    node.onDropFile = function(file) { return loadFile(file) || dropFile?.apply(this, arguments); };
    const executed = node.onExecuted;
    node.onExecuted = function(message) {
        const result = executed?.apply(this, arguments), preview = message?.liquify_source?.[0];
        if (!preview || removed || !inputConnected()) return result;
        finish(); const version = ++loadVersion; loading = true; updateControls();
        pendingLoad = decode(preview.data).then(image => {
            if (removed || version !== loadVersion || !inputConnected()) return;
            doc.source_mode = "input"; doc.source = ""; sourceJSON = '""';
            doc.preview = preview.data; previewJSON = JSON.stringify(preview.data);
            doc.width = preview.width; doc.height = preview.height; doc.batch = preview.batch;
            setPreview(image); loading = false; changed(false);
        }).catch(error => { if (!removed && version === loadVersion) info(error.message, true); })
            .finally(() => { if (!removed && version === loadVersion) { loading = false; updateControls(); } });
        return result;
    };
    const connections = node.onConnectionsChange;
    node.onConnectionsChange = function() { const result = connections?.apply(this, arguments); updateControls(); return result; };
    const configure = node.onConfigure;
    node.onConfigure = function() { const result = configure?.apply(this, arguments); pendingLoad = restore(); return result; };
    const onSerialize = node.onSerialize;
    node.onSerialize = function(saved) {
        const result = onSerialize?.apply(this, arguments);
        if (saved.widgets_values) saved.widgets_values[node.widgets.indexOf(widget)] = serialize();
        return result;
    };
    widget.serializeValue = async () => { await pendingLoad; if (restoreError) throw restoreError; return serialize(); };
    const onRemoved = node.onRemoved;
    node.onRemoved = function() {
        removed = true; loadVersion++; if (frame) cancelAnimationFrame(frame);
        active = null;
        if (pointer !== null && canvas.hasPointerCapture(pointer)) canvas.releasePointerCapture(pointer);
        window.removeEventListener("blur", finish); window.removeEventListener("blur", endCompare);
        model = pixels = resetHistory = null; doc = null; sourceJSON = previewJSON = "";
        canvas.width = canvas.height = 0; root.remove(); delete node.__liquify;
        return onRemoved?.apply(this, arguments);
    };
    node.__liquify = { root };
    pendingLoad = restore();
    node.size[0] = Math.max(node.size[0], 360); node.size[1] = Math.max(node.size[1], 440);
}

app.registerExtension({
    name: "WepeNerd.LiquifyImage",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "WN_LiquifyImage") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            const result = created?.apply(this, arguments); setupLiquify(this); return result;
        };
    },
});
