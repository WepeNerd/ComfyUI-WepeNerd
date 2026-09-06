import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

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
.wn-mask-editor .wn-mask-decision{flex-wrap:wrap;padding:8px;background:var(--comfy-menu-bg,#35363b);border-radius:4px}`;
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
export function setupMaskedLora(node) {
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
    let saved = node.properties.wnMask || { v: 1, open: false, brush: 60, reference: null };
    let reference = null, busy = false, removed = false, restoring = false;
    let gesture = null, pointer = null, pending = null, upstreamArmed = false;
    let history = [];
    const mask = element("canvas");
    mask.width = mask.height = 1024;
    const mctx = mask.getContext("2d", { willReadFrequently: true });
    const root = element("div", "wn-mask-editor");
    root.tabIndex = 0;
    root.setAttribute("aria-label", "Masked LoRA editor");
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
    const source = element("button");
    source.title = "Load a snapshot of the first image in the connected batch. Never runs downstream nodes.";
    toolbar.append(brush, rectangle, eraser, size, undo, clear);
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
    canvas.setAttribute("aria-label", "Paint mask here, or drop an image");
    const hint = element("span", "wn-mask-hint"); hint.textContent = "Drop image or paint here";
    stage.append(canvas, hint);
    const dimensions = element("div", "wn-mask-note");
    dimensions.title = "The mask maps proportionally to the output image grid. A blank square mask stretches on non-square outputs. Load an image with the intended aspect ratio for aligned painting.";
    const note = element("div", "wn-mask-note"); note.setAttribute("role", "status");
    const footer = element("div", "wn-mask-row wn-mask-between"); footer.append(dimensions, removeRef);
    body.append(toolbar, sourceRow, decision, stage, footer);
    root.append(row, body, note);
    const file = element("input"); file.type = "file"; file.accept = "image/*"; file.hidden = true; root.append(file);
    const dom = node.addDOMWidget("mask_editor", "wn_mask_editor", root, { serialize: false, hideOnZoom: false });
    let height = 35;
    dom.computeSize = () => [300, height];
    dom.options.getMinHeight = () => height;
    dom.options.getMaxHeight = () => height;

    function linked() { return node.inputs?.find(i => i.name === "image")?.link != null; }
    function painted() { return Boolean(data.value); }
    function remember() {
        const changed = Object.keys(saved).some(key => saved[key] !== node.properties.wnMask?.[key]);
        node.properties.wnMask = { ...saved };
        if (changed) node.graph?.change();
    }
    function fit() {
        body.hidden = !saved.open;
        toggle.textContent = saved.open ? "Hide mask" : "Edit mask";
        row.setAttribute("aria-expanded", String(saved.open));
        const canvasHeight = Math.min(480, (node.size[0] - 24) * mask.height / mask.width);
        height = saved.open ? canvasHeight + 170 + (decision.hidden ? 0 : 78) : 35;
        if (note.textContent) height += 30;
        root.style.height = `${height}px`;
        node.setSize([Math.max(360, node.size[0]), node.computeSize()[1]]);
        node.setDirtyCanvas(true, true);
        render();
    }
    function message(text) { note.textContent = text; fit(); }
    function refresh() {
        state.textContent = painted() ? "Painted" : "Empty";
        dimensions.textContent = `${mask.width} × ${mask.height} px`;
        source.textContent = linked() ? (upstreamArmed ? "Run upstream" : "Load input") : "Open image";
        source.disabled = busy;
        removeRef.hidden = !reference;
        sourceState.textContent = reference ? (saved.reference?.source || "Reference image") : "Blank canvas";
        hint.hidden = Boolean(reference) || painted();
        for (const button of toolButtons) button.setAttribute("aria-pressed", String(button.getAttribute("aria-label") === tool.value));
        undo.disabled = !history.length || busy;
        clear.disabled = !painted() || busy;
        size.disabled = tool.value === "Rectangle";
        size.title = `Brush diameter: ${size.value} native pixels`;
        render();
    }
    function serializeMask() {
        const bytes = mctx.getImageData(0, 0, mask.width, mask.height).data;
        let any = false;
        for (let i = 3; i < bytes.length; i += 4) if (bytes[i]) { any = true; break; }
        // Empty rasters retain dimensions in workflow properties without affecting inference.
        saved.width = mask.width; saved.height = mask.height;
        data.value = any ? JSON.stringify({ v: 1, width: mask.width, height: mask.height, png: mask.toDataURL("image/png") }) : "";
        remember(); node.graph?.change(); refresh();
    }
    function snapshot() { return { png: mask.toDataURL("image/png"), width: mask.width, height: mask.height, reference: saved.reference }; }
    function push(value) {
        history.push(value);
        // Bound undo by both action count and encoded byte size.
        let total = history.reduce((n, s) => n + s.png.length + (s.reference?.png?.length || 0), 0);
        while (history.length > 1 && (history.length > 20 || total > 64 * 1024 * 1024)) {
            const old = history.shift(); total -= old.png.length + (old.reference?.png?.length || 0);
        }
    }
    async function restore(value) {
        busy = true; refresh();
        try {
            const [bitmap, ref] = await Promise.all([readImage(value.png), value.reference ? readImage(value.reference.png) : null]);
            mask.width = value.width; mask.height = value.height;
            mctx.drawImage(bitmap, 0, 0); reference = ref; saved.reference = value.reference;
            serializeMask();
        } finally { busy = false; refresh(); fit(); }
    }
    function drawComposite(ctx, width, height) {
        ctx.clearRect(0, 0, width, height);
        if (reference) ctx.drawImage(reference, 0, 0, width, height);
        const tint = element("canvas"); tint.width = width; tint.height = height;
        const tctx = tint.getContext("2d");
        tctx.drawImage(mask, 0, 0, width, height);
        tctx.globalCompositeOperation = "source-in"; tctx.fillStyle = "#d84d9d"; tctx.fillRect(0, 0, width, height);
        ctx.globalAlpha = .45; ctx.drawImage(tint, 0, 0); ctx.globalAlpha = 1;
    }
    function render() {
        const available = Math.max(100, node.size[0] - 24);
        const scale = Math.min(available / mask.width, 480 / mask.height);
        const width = Math.round(mask.width * scale), h = Math.round(mask.height * scale);
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.max(1, Math.round(width * dpr)); canvas.height = Math.max(1, Math.round(h * dpr));
        canvas.style.width = `${width}px`; canvas.style.height = `${h}px`;
        const ctx = canvas.getContext("2d");
        drawComposite(ctx, canvas.width, canvas.height);
        if (gesture && tool.value === "Rectangle") {
            ctx.fillStyle = "#d84d9d"; ctx.globalAlpha = .45;
            ctx.fillRect(gesture.start.x / mask.width * canvas.width, gesture.start.y / mask.height * canvas.height,
                (gesture.last.x - gesture.start.x) / mask.width * canvas.width, (gesture.last.y - gesture.start.y) / mask.height * canvas.height);
            ctx.globalAlpha = 1;
        }
        if (pointer && tool.value !== "Rectangle") {
            ctx.beginPath(); ctx.arc(pointer.x / mask.width * canvas.width, pointer.y / mask.height * canvas.height, Number(size.value) / mask.width * canvas.width / 2, 0, Math.PI * 2);
            ctx.strokeStyle = "#000"; ctx.lineWidth = 3 * dpr; ctx.stroke();
            ctx.strokeStyle = "#fff"; ctx.lineWidth = dpr; ctx.stroke();
        }
        const tc = thumb.getContext("2d"); tc.clearRect(0, 0, 74, 50);
        const ts = Math.min(74 / mask.width, 50 / mask.height);
        tc.save(); tc.translate((74 - mask.width * ts) / 2, (50 - mask.height * ts) / 2);
        drawComposite(tc, mask.width * ts, mask.height * ts); tc.restore();
    }
    function coords(event) {
        const rect = canvas.getBoundingClientRect();
        return { x: Math.max(0, Math.min(mask.width, (event.clientX - rect.left) / rect.width * mask.width)), y: Math.max(0, Math.min(mask.height, (event.clientY - rect.top) / rect.height * mask.height)) };
    }
    function stroke(a, b) {
        mctx.globalCompositeOperation = tool.value === "Eraser" ? "destination-out" : "source-over";
        mctx.strokeStyle = "white"; mctx.fillStyle = "white";
        mctx.lineWidth = Number(size.value); mctx.lineCap = mctx.lineJoin = "round";
        mctx.beginPath(); mctx.moveTo(a.x, a.y); mctx.lineTo(b.x, b.y); mctx.stroke();
        if (a.x === b.x && a.y === b.y) { mctx.beginPath(); mctx.arc(a.x, a.y, Number(size.value) / 2, 0, 2 * Math.PI); mctx.fill(); }
        mctx.globalCompositeOperation = "source-over";
    }
    async function finish(commit) {
        if (!gesture) return;
        const action = gesture; gesture = null;
        if (canvas.hasPointerCapture(action.id)) canvas.releasePointerCapture(action.id);
        if (!commit) { await restore(action.before); return; }
        if (tool.value === "Rectangle") {
            mctx.fillStyle = "white";
            mctx.fillRect(Math.min(action.start.x, action.last.x), Math.min(action.start.y, action.last.y), Math.abs(action.last.x - action.start.x), Math.abs(action.last.y - action.start.y));
        }
        push(action.before); serializeMask();
    }
    canvas.onpointerdown = event => {
        if (busy || restoring || pending || event.button !== 0) return;
        root.focus(); event.preventDefault(); event.stopPropagation();
        const point = coords(event); gesture = { id: event.pointerId, start: point, last: point, before: snapshot() };
        canvas.setPointerCapture(event.pointerId);
        if (tool.value !== "Rectangle") stroke(point, point);
        pointer = point; render();
    };
    canvas.onpointermove = event => {
        pointer = coords(event);
        if (gesture && gesture.id === event.pointerId) {
            if (tool.value !== "Rectangle") stroke(gesture.last, pointer);
            gesture.last = pointer;
        }
        render();
    };
    canvas.onpointerup = event => { if (gesture?.id === event.pointerId) void finish(true); };
    canvas.onpointercancel = () => void finish(false);
    canvas.onlostpointercapture = () => void finish(false);
    canvas.onpointerleave = () => { if (!gesture) { pointer = null; render(); } };
    const blur = () => { pointer = null; void finish(false); };
    window.addEventListener("blur", blur);
    root.onkeydown = event => {
        if (event.key === "Escape") { event.stopPropagation(); void finish(false); }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.stopPropagation(); undo.click(); }
    };
    for (const name of ["pointerdown", "pointermove", "pointerup", "wheel", "dblclick"]) root.addEventListener(name, e => e.stopPropagation());
    row.onclick = async () => { await finish(false); saved.open = !saved.open; remember(); fit(); };
    size.oninput = () => { saved.brush = Number(size.value); remember(); refresh(); };
    for (const button of toolButtons) button.onclick = async () => { await finish(false); tool.value = button.getAttribute("aria-label"); refresh(); };
    undo.onclick = async () => { if (gesture || busy || !history.length) return; await restore(history.pop()); };
    clear.onclick = () => { if (busy || gesture) return; push(snapshot()); mctx.clearRect(0, 0, mask.width, mask.height); serializeMask(); };
    removeRef.onclick = () => { if (busy || gesture) return; push(snapshot()); reference = null; saved.reference = null; remember(); refresh(); fit(); };
    const oldMenu = node.getExtraMenuOptions;
    node.getExtraMenuOptions = function(...args) { oldMenu?.apply(this, args); args[1].push({content:"Open reference image…",callback:() => file.click()}); };

    async function adopt(image, asset) {
        await finish(false);
        const apply = () => {
            push(snapshot());
            if (image.naturalWidth !== mask.width || image.naturalHeight !== mask.height) { mask.width = image.naturalWidth; mask.height = image.naturalHeight; }
            reference = image; saved.reference = asset; saved.open = true; note.textContent = "";
            serializeMask(); fit();
        };
        if (painted() && (image.naturalWidth !== mask.width || image.naturalHeight !== mask.height)) {
            pending = apply; saved.open = true; decision.hidden = false; fit();
        } else apply();
    }
    replace.onclick = () => { const action = pending; pending = null; decision.hidden = true; action?.(); };
    cancel.onclick = () => { pending = null; decision.hidden = true; fit(); };
    async function importImage(blob, sourceName = "Dropped / opened image") {
        if (busy || pending) return;
        busy = true; refresh();
        try {
            // Browser decoding applies EXIF orientation before the exact-size PNG snapshot.
            const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
            const raster = element("canvas"); raster.width = bitmap.width; raster.height = bitmap.height;
            raster.getContext("2d").drawImage(bitmap, 0, 0); bitmap.close();
            const png = raster.toDataURL("image/png");
            const image = await readImage(png);
            const upload = await new Promise(resolve => raster.toBlob(resolve, "image/png"));
            const form = new FormData(); form.append("image", upload, `mask-reference-${crypto.randomUUID()}.png`); form.append("subfolder", "wepenerd_masked_lora"); form.append("type", "input");
            const response = await api.fetchApi("/upload/image", { method: "POST", body: form });
            if (!response.ok) throw new Error("Could not save reference image; existing painting was kept.");
            const asset = await response.json();
            await adopt(image, { png, asset, source: sourceName });
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
        if (!linked()) { file.click(); return; }
        const link = node.graph.links[node.inputs.find(i => i.name === "image").link];
        const output = app.nodeOutputs?.[link.origin_id];
        if (output?.images?.[0] && !upstreamArmed) {
            const response = await api.fetchApi(`/view?${new URLSearchParams(output.images[0])}`);
            if (!response.ok) throw new Error("Upstream preview is unavailable. Run upstream to load image.");
            await importImage(await response.blob(), "Input image"); return;
        }
        if (!upstreamArmed) { upstreamArmed = true; message("Run upstream to load image (first image of batch)."); refresh(); return; }
        const { output: full } = await app.graphToPrompt();
        const subset = {}, visiting = new Set();
        function visit(id) {
            id = String(id);
            if (id === String(node.id) || visiting.has(id)) throw new Error("IMAGE creates a cycle through this MODEL chain. Use a saved image.");
            if (subset[id]) return;
            if (!full[id]) throw new Error("Upstream image source is unavailable.");
            visiting.add(id);
            for (const value of Object.values(full[id].inputs)) if (Array.isArray(value) && value.length === 2 && full[String(value[0])]) visit(value[0]);
            visiting.delete(id); subset[id] = full[id];
        }
        visit(link.origin_id);
        const sink = `wn_mask_snapshot_${crypto.randomUUID()}`;
        subset[sink] = { class_type: "WN_MaskedLoraSnapshot", inputs: { image: [String(link.origin_id), link.origin_slot] } };
        busy = true; refresh();
        let promptId;
        const done = async event => {
            if (String(event.detail.node) !== sink || removed) return;
            cleanup(); busy = false;
            try {
                const response = await api.fetchApi(`/view?${new URLSearchParams(event.detail.output.images[0])}`);
                if (!response.ok) throw new Error("Could not read upstream snapshot.");
                await importImage(await response.blob(), "Input image");
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
            const queued = await api.queuePrompt(0, { output: subset, workflow: { nodes: [], links: [] } });
            promptId = queued.prompt_id;
            message("Loading upstream image…");
        } catch (error) { cleanup(); busy = false; refresh(); throw error; }
    }
    let pendingCleanup = null;
    source.onclick = () => loadInput().catch(error => message(error.message));
    const oldConnections = node.onConnectionsChange;
    node.onConnectionsChange = function(...args) { oldConnections?.apply(this, args); upstreamArmed = false; refresh(); };
    const oldResize = node.onResize;
    let previousWidth = node.size[0];
    node.onResize = function(...args) {
        oldResize?.apply(this, args);
        if (previousWidth !== node.size[0]) { previousWidth = node.size[0]; queueMicrotask(fit); }
        render();
    };
    const oldRemoved = node.onRemoved;
    node.onRemoved = function(...args) { removed = true; gesture = null; pendingCleanup?.(); window.removeEventListener("blur", blur); root.remove(); oldRemoved?.apply(this, args); };
    const oldConfigure = node.onConfigure;
    node.onConfigure = function(...args) { oldConfigure?.apply(this, args); void hydrate(); };
    async function hydrate() {
        restoring = true;
        try {
            saved = { v: 1, open: false, brush: 60, ...node.properties.wnMask };
            size.value = saved.brush;
            const maskData = data.value ? JSON.parse(data.value) : null;
            mask.width = maskData?.width || saved.width || 1024; mask.height = maskData?.height || saved.height || 1024;
            if (maskData) mctx.drawImage(await readImage(maskData.png), 0, 0);
            reference = saved.reference ? await readImage(saved.reference.png) : null;
            history = []; refresh(); fit();
        } catch (error) { message(`Cannot restore mask: ${error.message}`); }
        finally { restoring = false; }
    }
    void hydrate();
}

app.registerExtension({
    name: "wepenerd.masked_lora",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function(...args) { created?.apply(this, args); setupMaskedLora(this); };
    },
});
