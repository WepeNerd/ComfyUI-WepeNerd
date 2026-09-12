import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "WN_Speedpaint";
const editors = new Set();
const HISTORY_BYTES = 128 * 1024 * 1024;
const style = document.createElement("style");
style.textContent = `
.wn-speedpaint{display:flex;flex-direction:column;gap:5px;width:100%;height:100%;padding:0 5px 6px;box-sizing:border-box;font:12px var(--comfy-font-family,Arial,sans-serif);color:var(--input-text,#ddd)}
.wn-speedpaint *{box-sizing:border-box}.wn-speedpaint [hidden]{display:none!important}
.wn-speedpaint .sp-row{display:flex;align-items:center;gap:5px;flex:none;min-height:27px}
.wn-speedpaint button,.wn-speedpaint input{font:inherit;color:inherit;background:var(--comfy-input-bg,#27282d);border:1px solid var(--border-color,#515158);border-radius:4px}
.wn-speedpaint button{height:27px;padding:3px 8px;cursor:pointer;white-space:nowrap}.wn-speedpaint button:hover{background:var(--comfy-menu-bg,#414148)}
.wn-speedpaint button:disabled{opacity:.4;cursor:default}.wn-speedpaint button[aria-pressed=true]{color:#f2c0db;border-color:#bb7f9d;background:#41313b}
.wn-speedpaint button.sp-icon{width:28px;padding:5px;display:grid;place-items:center;flex:none}.wn-speedpaint svg{width:16px;height:16px;display:block}
.wn-speedpaint input[type=color]{width:28px;height:27px;padding:2px;cursor:pointer;flex:none}.wn-speedpaint input[type=range]{flex:1;min-width:38px;width:70px;margin:0;accent-color:#d69ab9;cursor:pointer}
.wn-speedpaint input[type=number]{width:49px;height:27px;padding:3px}.wn-speedpaint input.sp-hex{width:68px;height:27px;padding:3px;font-size:11px}
.wn-speedpaint .sp-size{font-variant-numeric:tabular-nums;font-size:11px;min-width:36px;text-align:right;white-space:nowrap}
.wn-speedpaint .sp-status{font-size:10px;color:var(--descrip-text,#aaa);margin-right:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.wn-speedpaint .sp-status.sp-error{color:#ff9b99}.wn-speedpaint .sp-stage{flex:1;min-height:160px;position:relative;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#191a1d;border:1px solid var(--border-color,#414147);border-radius:3px}
.wn-speedpaint .sp-picture{position:relative;flex:none}.wn-speedpaint canvas{display:block;width:100%;height:100%;touch-action:none;cursor:none}
.wn-speedpaint .sp-cursor{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:hidden}.wn-speedpaint .sp-stage.sp-busy canvas{cursor:progress}
.wn-speedpaint .sp-picker{display:flex;gap:4px;position:absolute;bottom:38px;left:5px;padding:7px;background:var(--comfy-menu-bg,#303138);border:1px solid var(--border-color,#575760);border-radius:5px;z-index:2}
.wn-speedpaint .sp-tools{gap:4px;position:relative}.wn-speedpaint .sp-divider{width:1px;height:17px;background:var(--border-color,#515158);margin:0 1px;flex:none}
.wn-speedpaint .sp-shortcuts{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;padding:0;border:0}
`;
document.head.append(style);

function element(tag, className = "") {
    const el = document.createElement(tag);
    el.className = className;
    return el;
}
function button(label, paths) {
    const el = element("button", paths ? "sp-icon" : "");
    el.type = "button";
    el.title = label;
    el.setAttribute("aria-label", label);
    if (paths) {
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" })) svg.setAttribute(key, value);
        const path = document.createElementNS(svg.namespaceURI, "path");
        path.setAttribute("d", paths);
        svg.append(path); el.append(svg);
    } else el.textContent = label;
    return el;
}
function imageURL(asset) {
    return api.apiURL(`/view?${new URLSearchParams({ filename: asset, subfolder: "wepenerd_speedpaint", type: "input" })}`);
}
function readImage(url) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error("Could not restore painting. Check input/wepenerd_speedpaint."));
        image.src = url;
    });
}
async function request(operation, body) {
    const response = await api.fetchApi(`/wepenerd/speedpaint/${operation}`, {
        method: "POST", body: body instanceof FormData ? body : JSON.stringify(body),
        ...(body instanceof FormData ? {} : { headers: { "Content-Type": "application/json" } }),
    });
    if (!response.ok) {
        const text = await response.text();
        let message = text;
        try { message = JSON.parse(text).error || text; } catch { /* Non-JSON server error. */ }
        throw new Error(message || `Speedpaint request failed (${response.status}).`);
    }
    return response.json();
}
function clone(value) { return JSON.parse(JSON.stringify(value)); }
function checkSize(width, height) {
    if (![width, height].every(v => Number.isInteger(v) && v >= 64 && v <= 4096)) throw new Error("Use integer dimensions from 64 to 4096 pixels.");
}

export function setupSpeedpaint(node) {
    const data = node.widgets.find(w => w.name === "document");
    Object.assign(data, { type: "wn_hidden", hidden: true, computeSize: () => [0, -4], draw() {}, mouse: () => false });
    const dimensions = ["width", "height"].map(name => node.widgets.find(w => w.name === name));
    node.properties ||= {};
    const defaults = { colour: "#24232b", background: "#e8e6e1", size: 32, opacity: 100, shape: "round", pressure: true, local: [1024, 1024] };
    let settings = { ...defaults, ...clone(node.properties.speedpaint || {}) };
    let doc = null, derived = null, removed = false, busy = 0, revision = 0;
    let pending = Promise.resolve(), savePromise = null, saveTimer = null, saveError = null;
    let history = [], future = [], historyBytes = 0;
    let stroke = null, cropGesture = null, frame = null, cursorPoint = null;
    let executionId = null, previewToken = 0;
    const root = element("div", "wn-speedpaint"); root.tabIndex = 0; root.setAttribute("aria-label", "Speedpaint editor");
    // A text input owns painting shortcuts so ComfyUI's capture-phase graph undo leaves them alone.
    const shortcuts = element("textarea", "sp-shortcuts"); shortcuts.tabIndex = -1; shortcuts.setAttribute("aria-label", "Painting keyboard shortcuts");
    root.addEventListener("focus", () => shortcuts.focus({ preventScroll: true }));
    root.addEventListener("click", event => { if (event.target.closest("button")) shortcuts.focus({ preventScroll: true }); });
    const setup = element("div", "sp-row");
    const status = element("span", "sp-status"); status.setAttribute("role", "status");
    const background = element("input"); background.type = "color"; background.title = "Background for New and image import"; background.setAttribute("aria-label", "Background colour");
    const fresh = button("New"), load = button("Load");
    load.title = "Load or drop PNG, JPEG or WebP. Shift-drag to reposition before painting.";
    setup.append(status, background, fresh, load);
    const stage = element("div", "sp-stage");
    const picture = element("div", "sp-picture");
    const canvas = element("canvas"); canvas.setAttribute("aria-label", "Painting canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const base = element("canvas"), mask = element("canvas");
    const bctx = base.getContext("2d", { willReadFrequently: true }), mctx = mask.getContext("2d");
    const cursor = document.createElementNS("http://www.w3.org/2000/svg", "svg"); cursor.classList.add("sp-cursor"); cursor.hidden = true;
    const outline = document.createElementNS(cursor.namespaceURI, "path");
    outline.setAttribute("fill", "none"); outline.setAttribute("stroke", "white"); outline.setAttribute("stroke-width", "1"); outline.setAttribute("vector-effect", "non-scaling-stroke");
    outline.style.filter = "drop-shadow(0 0 1px black)"; cursor.append(outline);
    picture.append(canvas, cursor); stage.append(picture);
    const tools = element("div", "sp-row sp-tools");
    const round = button("Round brush", "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z");
    const square = button("Square brush", "M4 4h16v16H4Z");
    const colour = button("Brush colour"); colour.className = "sp-icon"; colour.style.borderWidth = "3px";
    colour.textContent = "";
    const picker = element("div", "sp-picker"); picker.hidden = true;
    const swatch = element("input"); swatch.type = "color"; swatch.setAttribute("aria-label", "Brush colour picker");
    const hex = element("input", "sp-hex"); hex.type = "text"; hex.maxLength = 7; hex.setAttribute("aria-label", "Brush hex colour");
    picker.append(swatch, hex);
    const size = element("input"); size.type = "range"; size.min = "1"; size.max = "512"; size.step = "1"; size.setAttribute("aria-label", "Brush size"); size.title = "Brush size in image pixels · [ and ]";
    const sizeLabel = element("span", "sp-size");
    const opacity = element("input"); opacity.type = "number"; opacity.min = "1"; opacity.max = "100"; opacity.step = "1"; opacity.title = "Stroke opacity (%)"; opacity.setAttribute("aria-label", "Opacity percent");
    const percent = element("span"); percent.textContent = "%";
    const pressure = button("Pen pressure controls size", "m15 3 6 6M4 20l4-1 12-12a2.1 2.1 0 0 0-3-3L5 16l-1 4Z");
    const undo = button("Undo · Ctrl/Cmd+Z", "M9 5 4 10l5 5M4 10h10a6 6 0 0 1 0 12");
    const divider = element("span", "sp-divider");
    tools.append(round, square, colour, divider, size, sizeLabel, opacity, percent, pressure, undo, picker);
    const file = element("input"); file.type = "file"; file.accept = "image/png,image/jpeg,image/webp"; file.hidden = true;
    root.append(setup, stage, tools, file, shortcuts);
    const dom = node.addDOMWidget("speedpaint_editor", "wn_speedpaint", root, { serialize: false, hideOnZoom: false, getMinHeight: () => 255 });
    dom.computeSize = () => [390, Math.max(255, node.size[1] - 95)];
    node.size = [Math.max(430, node.size[0]), Math.max(575, node.size[1])];

    function linked(index) { return node.inputs?.find(i => i.name === dimensions[index].name)?.link != null; }
    function remember() { node.properties.speedpaint = clone(settings); }
    function message(value, error = false) {
        status.textContent = value; status.title = value; status.classList.toggle("sp-error", error);
    }
    function refresh() {
        background.value = settings.background; swatch.value = hex.value = settings.colour;
        colour.style.background = settings.colour;
        size.value = settings.size; sizeLabel.textContent = `${settings.size}px`; opacity.value = settings.opacity;
        round.setAttribute("aria-pressed", String(settings.shape === "round")); square.setAttribute("aria-pressed", String(settings.shape === "square"));
        pressure.setAttribute("aria-pressed", String(settings.pressure));
        fresh.disabled = load.disabled = undo.disabled = busy > 0;
        undo.disabled ||= !history.length;
        stage.classList.toggle("sp-busy", busy > 0);
        if (busy) message("Preparing…");
        else if (saveError) message(saveError.message, true);
        else if (doc) message(`${canvas.width} × ${canvas.height}${dimensions.some((_, i) => linked(i)) ? " · linked / last resolved" : ""}`);
        remember(); drawCursor();
    }
    function fit() {
        const w = stage.clientWidth, h = stage.clientHeight;
        const scale = Math.min(w / canvas.width, h / canvas.height);
        picture.style.width = `${Math.max(1, canvas.width * scale)}px`;
        picture.style.height = `${Math.max(1, canvas.height * scale)}px`;
    }
    const observer = new ResizeObserver(fit); observer.observe(stage);
    function setDimensions(w, h) {
        canvas.width = base.width = mask.width = w; canvas.height = base.height = mask.height = h;
        cursor.setAttribute("viewBox", `0 0 ${w} ${h}`); fit();
    }
    function sync() {
        data.value = JSON.stringify(doc); revision++; previewToken++;
        saveError = null; node.setDirtyCanvas(true, true); refresh();
        if (node.graph) app.extensionManager.workflow.activeWorkflow?.changeTracker?.captureCanvasState();
    }
    function capture() {
        doc = { ...doc, width: canvas.width, height: canvas.height, inline: canvas.toDataURL("image/png") };
        delete doc.asset;
        sync(); scheduleSave();
    }
    async function display(document) {
        const image = document.inline || document.asset ? await readImage(document.inline || imageURL(document.asset)) : null;
        if (removed) return;
        setDimensions(document.width, document.height);
        if (image) ctx.drawImage(image, 0, 0);
        else { ctx.fillStyle = document.background; ctx.fillRect(0, 0, canvas.width, canvas.height); }
    }
    function pushHistory(entry) {
        future = [];
        entry.bytes = entry.kind === "stroke" ? entry.before.data.byteLength + entry.after.data.byteLength : 2 * JSON.stringify(entry).length;
        history.push(entry); historyBytes += entry.bytes;
        while (history.length > 30 || historyBytes > HISTORY_BYTES) historyBytes -= history.shift().bytes;
        refresh();
    }
    function scheduleSave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => save().catch(error => { saveError = error; refresh(); }), 100);
    }
    async function save() {
        if (!doc?.inline) return;
        if (savePromise) await savePromise;
        if (!doc?.inline) return;
        const committed = doc, version = revision;
        savePromise = request("commit", { document: committed });
        try {
            const result = await savePromise;
            if (!removed && version === revision) { doc = result; data.value = JSON.stringify(doc); saveError = null; }
        } catch (error) { saveError = error; throw error; }
        finally { savePromise = null; refresh(); }
    }
    async function flush() {
        endStroke(); endCrop();
        let preparation;
        do {
            preparation = pending;
            await preparation;
            await save();
        } while ((doc?.inline || pending !== preparation) && !removed);
        if (saveError) throw saveError;
    }
    function operate(action) {
        endStroke(); busy++; refresh();
        pending = pending.catch(() => {}).then(action).catch(error => { saveError = error; throw error; }).finally(() => { busy--; refresh(); });
        pending.catch(() => {});
        return pending;
    }
    async function replace(next, before = doc) {
        await display(next);
        if (removed) return;
        doc = next; derived = null;
        pushHistory({ kind: "document", before: clone(before), after: clone(next) }); sync();
    }
    function editingSize() {
        const result = dimensions.map((widget, i) => linked(i) ? canvas[i ? "height" : "width"] : Number(widget.value));
        checkSize(...result); return result;
    }
    function resize() {
        return operate(async () => {
            const [width, height] = editingSize();
            if (width === canvas.width && height === canvas.height && !derived) return;
            await replace(await request("prepare", { document: doc, width, height }));
        });
    }
    async function loadFile(upload) {
        if (!upload) return;
        return operate(async () => {
            const [w, h] = editingSize();
            const form = new FormData(); form.append("image", upload); form.append("width", w); form.append("height", h); form.append("background", settings.background);
            await replace(await request("import", form));
        });
    }
    function adoptDerived() {
        if (!derived) return;
        const previous = clone(doc);
        doc = { ...doc, asset: derived.asset, width: derived.width, height: derived.height, painted: true };
        delete doc.inline; derived = null;
        pushHistory({ kind: "document", before: previous, after: clone(doc) }); sync();
    }
    function point(event) {
        const rect = canvas.getBoundingClientRect();
        return { x: (event.clientX - rect.left) * canvas.width / rect.width, y: (event.clientY - rect.top) * canvas.height / rect.height,
            pressure: event.pointerType === "pen" && settings.pressure ? Math.max(0, event.pressure) : 1 };
    }
    function diameter(p, maximum = settings.size) { return maximum * (0.1 + 0.9 * p.pressure); }
    function drawCursor() {
        cursor.style.display = cursorPoint && !busy ? "block" : "none";
        if (!cursorPoint) return;
        const { x, y } = cursorPoint, r = diameter(cursorPoint) / 2;
        outline.setAttribute("d", settings.shape === "square" ? `M${x-r} ${y-r}h${2*r}v${2*r}h${-2*r}Z` : `M${x+r} ${y}a${r} ${r} 0 1 0 ${-2*r} 0a${r} ${r} 0 1 0 ${2*r} 0`);
    }
    function stamp(p) {
        if (p.pressure <= 0) return;
        const radius = diameter(p, stroke.size) / 2;
        if (stroke.shape === "square") stroke.path.rect(p.x-radius, p.y-radius, radius*2, radius*2);
        else { stroke.path.moveTo(p.x+radius, p.y); stroke.path.arc(p.x, p.y, radius, 0, 2*Math.PI); }
        stroke.bounds[0] = Math.min(stroke.bounds[0], p.x-radius-2); stroke.bounds[1] = Math.min(stroke.bounds[1], p.y-radius-2);
        stroke.bounds[2] = Math.max(stroke.bounds[2], p.x+radius+2); stroke.bounds[3] = Math.max(stroke.bounds[3], p.y+radius+2);
        stroke.painted = true;
    }
    function interpolate(p) {
        const last = stroke.last;
        const distance = Math.hypot(p.x-last.x, p.y-last.y);
        const steps = Math.max(1, Math.ceil(distance / Math.max(.25, Math.min(diameter(last, stroke.size), diameter(p, stroke.size)) * .18)));
        for (let i = 1; i <= steps; i++) {
            const t = i / steps;
            stamp({ x: last.x + (p.x-last.x)*t, y: last.y + (p.y-last.y)*t, pressure: last.pressure + (p.pressure-last.pressure)*t });
        }
        stroke.last = p;
    }
    function renderStroke() {
        frame = null;
        if (!stroke) return;
        mctx.clearRect(0, 0, mask.width, mask.height);
        mctx.fillStyle = stroke.colour; mctx.fill(stroke.path);
        ctx.globalAlpha = 1; ctx.drawImage(base, 0, 0);
        ctx.globalAlpha = stroke.opacity; ctx.drawImage(mask, 0, 0); ctx.globalAlpha = 1;
    }
    function endStroke(cancel = false) {
        if (!stroke) return;
        if (frame) { cancelAnimationFrame(frame); frame = null; }
        if (cancel) ctx.drawImage(base, 0, 0); else renderStroke();
        const ended = stroke; stroke = null;
        if (canvas.hasPointerCapture(ended.pointer)) canvas.releasePointerCapture(ended.pointer);
        if (cancel || !ended.painted) return;
        const [left, top, right, bottom] = ended.bounds;
        const x = Math.max(0, Math.floor(left)), y = Math.max(0, Math.floor(top));
        const w = Math.min(canvas.width, Math.ceil(right)) - x, h = Math.min(canvas.height, Math.ceil(bottom)) - y;
        if (w <= 0 || h <= 0) return;
        pushHistory({ kind: "stroke", x, y, before: bctx.getImageData(x, y, w, h), after: ctx.getImageData(x, y, w, h), paintedBefore: doc.painted });
        doc.painted = true; capture();
    }
    function endCrop(cancel = false) {
        if (!cropGesture) return;
        const gesture = cropGesture; cropGesture = null;
        if (canvas.hasPointerCapture(gesture.pointer)) canvas.releasePointerCapture(gesture.pointer);
        if (cancel || !gesture.crop) return;
        operate(async () => {
            const next = { ...doc, crop: gesture.crop };
            await replace(await request("prepare", { document: next, width: canvas.width, height: canvas.height }));
        });
    }
    canvas.addEventListener("pointerdown", event => {
        event.stopPropagation(); event.preventDefault(); root.focus({ preventScroll: true });
        if (busy || !doc || stroke || cropGesture || event.button !== 0) return;
        picker.hidden = true;
        const p = point(event); cursorPoint = p;
        if (event.altKey) {
            const rgb = ctx.getImageData(Math.max(0, Math.min(canvas.width-1, Math.floor(p.x))), Math.max(0, Math.min(canvas.height-1, Math.floor(p.y))), 1, 1).data;
            settings.colour = "#" + [...rgb].slice(0, 3).map(v => v.toString(16).padStart(2, "0")).join(""); refresh(); return;
        }
        if (event.shiftKey && doc.source && !doc.painted && !derived) {
            cropGesture = { pointer: event.pointerId, start: p, crop: null, initial: [...doc.crop] };
            canvas.setPointerCapture(event.pointerId); return;
        }
        adoptDerived();
        bctx.drawImage(canvas, 0, 0);
        stroke = { pointer: event.pointerId, path: new Path2D(), last: p, size: settings.size, shape: settings.shape,
            colour: settings.colour, opacity: settings.opacity/100, bounds: [Infinity, Infinity, -Infinity, -Infinity], painted: false };
        canvas.setPointerCapture(event.pointerId); stamp(p); frame = requestAnimationFrame(renderStroke);
    });
    canvas.addEventListener("pointermove", event => {
        event.stopPropagation();
        cursorPoint = point(event); drawCursor();
        if (cropGesture?.pointer === event.pointerId) {
            const scale = Math.max(canvas.width/doc.source_width, canvas.height/doc.source_height);
            const extra = [doc.source_width-canvas.width/scale, doc.source_height-canvas.height/scale];
            const shift = [cursorPoint.x-cropGesture.start.x, cursorPoint.y-cropGesture.start.y];
            cropGesture.crop = extra.map((v, i) => v > 0 ? Math.max(0, Math.min(1, cropGesture.initial[i]-shift[i]/scale/v)) : .5);
            message("Release to apply crop"); return;
        }
        if (stroke?.pointer !== event.pointerId) return;
        const samples = event.getCoalescedEvents?.();
        for (const sample of samples?.length ? samples : [event]) interpolate(point(sample));
        if (!frame) frame = requestAnimationFrame(renderStroke);
    });
    canvas.addEventListener("pointerup", event => {
        event.stopPropagation();
        if (stroke?.pointer === event.pointerId) endStroke();
        if (cropGesture?.pointer === event.pointerId) endCrop();
    });
    for (const name of ["pointercancel", "lostpointercapture"]) canvas.addEventListener(name, event => {
        if (stroke?.pointer === event.pointerId) endStroke(true);
        if (cropGesture?.pointer === event.pointerId) endCrop(true);
    });
    canvas.addEventListener("pointerleave", () => { cursorPoint = null; drawCursor(); });
    const blur = () => { endStroke(); endCrop(true); cursorPoint = null; drawCursor(); };
    const visibility = () => { if (document.hidden) blur(); };
    window.addEventListener("blur", blur); document.addEventListener("visibilitychange", visibility);
    for (const name of ["pointerdown", "pointermove", "pointerup", "dblclick", "wheel", "contextmenu"]) root.addEventListener(name, event => event.stopPropagation());
    stage.addEventListener("dragover", event => { event.preventDefault(); event.stopPropagation(); });
    stage.addEventListener("drop", event => { event.preventDefault(); event.stopPropagation(); loadFile(event.dataTransfer.files[0])?.catch(() => {}); });
    load.onclick = () => file.click();
    file.onchange = () => { loadFile(file.files[0])?.catch(() => {}); file.value = ""; };
    fresh.onclick = () => operate(async () => {
        const [width, height] = editingSize();
        await replace(await request("prepare", { document: { v: 1, background: settings.background, painted: false, crop: [.5, .5] }, width, height }));
    });
    background.oninput = () => { settings.background = background.value; remember(); };
    round.onclick = () => { settings.shape = "round"; refresh(); };
    square.onclick = () => { settings.shape = "square"; refresh(); };
    pressure.onclick = () => { settings.pressure = !settings.pressure; refresh(); };
    colour.onclick = () => { picker.hidden = !picker.hidden; };
    swatch.oninput = () => { settings.colour = swatch.value; refresh(); };
    hex.onchange = () => { if (/^#[0-9a-f]{6}$/i.test(hex.value)) settings.colour = hex.value; refresh(); };
    size.oninput = () => { settings.size = Number(size.value); refresh(); };
    opacity.onchange = () => { settings.opacity = Math.max(1, Math.min(100, Number(opacity.value) || 100)); refresh(); };

    function travel(redo = false) {
        if (busy) return;
        endStroke();
        return operate(async () => {
            const from = redo ? future : history, to = redo ? history : future;
            const entry = from.at(-1); if (!entry) return;
            if (derived) { await display(doc); derived = null; }
            if (entry.kind === "stroke") {
                ctx.putImageData(redo ? entry.after : entry.before, entry.x, entry.y);
                doc.painted = redo ? true : entry.paintedBefore; capture();
            } else {
                const next = clone(redo ? entry.after : entry.before); await display(next); doc = next; sync(); scheduleSave();
                dimensions.forEach((widget, i) => {
                    if (!linked(i)) { widget.value = settings.local[i] = doc[i ? "height" : "width"]; }
                });
            }
            from.pop(); to.push(entry); historyBytes += redo ? entry.bytes : -entry.bytes; refresh();
        });
    }
    undo.onclick = () => travel();
    root.addEventListener("keydown", event => {
        if (event.target !== shortcuts) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.stopPropagation(); travel(event.shiftKey); }
        if (event.key === "[" || event.key === "]") { event.preventDefault(); event.stopPropagation(); settings.size = Math.max(1, Math.min(512, settings.size + (event.key === "]" ? 1 : -1))); refresh(); }
        if (!event.ctrlKey && !event.metaKey && event.key.length === 1) event.preventDefault();
    });
    dimensions.forEach((widget, i) => {
        const callback = widget.callback;
        widget.callback = function(...args) {
            const result = callback?.apply(this, args);
            if (!linked(i) && doc) { settings.local[i] = Number(widget.value); remember(); resize(); }
            return result;
        };
    });
    const connections = node.onConnectionsChange;
    node.onConnectionsChange = function(...args) {
        const result = connections?.apply(this, args);
        if (args[0] === 1 && args[3]?.target_id === this.id) queueMicrotask(() => {
            dimensions.forEach((widget, i) => { if (!linked(i)) widget.value = settings.local[i]; });
            if (!args[2] && doc) resize(); else refresh();
        });
        return result;
    };
    async function restore() {
        const serialized = data.value;
        settings = { ...defaults, ...clone(node.properties.speedpaint || {}) };
        const next = serialized ? JSON.parse(serialized) : { v: 1, background: settings.background, painted: false, crop: [.5, .5], width: Number(dimensions[0].value), height: Number(dimensions[1].value) };
        checkSize(next.width, next.height);
        await display(next);
        doc = next; derived = null; history = []; future = []; historyBytes = 0; sync();
        if (!doc.asset && !doc.inline) capture(); else if (doc.inline) scheduleSave();
    }
    const configured = node.onConfigure;
    node.onConfigure = function(...args) {
        configured?.apply(this, args);
        settings = { ...defaults, ...clone(node.properties.speedpaint || {}) };
        operate(restore);
    };
    const serialized = node.onSerialize;
    node.onSerialize = function(info) {
        endStroke(); remember(); serialized?.call(this, info);
        // Serialization is synchronous in LiteGraph. Keep a lossless recovery PNG until the durable write completes.
        if (info.widgets_values) info.widgets_values[node.widgets.indexOf(data)] = data.value;
        info.properties.speedpaint = clone(settings);
    };
    const executionStart = event => { executionId = event.detail.prompt_id; };
    const executed = async event => {
        const detail = event.detail;
        if (String(detail.node) !== String(node.id) || (executionId && detail.prompt_id !== executionId)) return;
        const result = detail.output?.speedpaint?.[0];
        if (!result || result.document !== data.value || busy || stroke || cropGesture) return;
        const token = ++previewToken, version = revision;
        try {
            const image = await readImage(imageURL(result.asset));
            if (removed || token !== previewToken || version !== revision || busy || stroke || cropGesture) return;
            setDimensions(result.width, result.height); ctx.drawImage(image, 0, 0);
            derived = result.width === doc.width && result.height === doc.height ? null : result;
            refresh();
        } catch (error) { message(error.message, true); }
    };
    api.addEventListener("execution_start", executionStart); api.addEventListener("executed", executed);
    const editor = { node, flush, lock: () => { endStroke(); busy++; refresh(); }, unlock: () => { busy--; refresh(); } };
    editors.add(editor);
    const onRemoved = node.onRemoved;
    node.onRemoved = function(...args) {
        endStroke(); removed = true; clearTimeout(saveTimer); observer.disconnect(); editors.delete(editor);
        window.removeEventListener("blur", blur); document.removeEventListener("visibilitychange", visibility);
        api.removeEventListener("execution_start", executionStart); api.removeEventListener("executed", executed);
        root.remove(); return onRemoved?.apply(this, args);
    };
    queueMicrotask(() => { if (!doc && !busy) operate(restore); });
    refresh();
    return editor;
}

app.registerExtension({
    name: "wepenerd.speedpaint",
    async setup() {
        const prepare = original => async function(...args) {
            const active = [...editors].filter(editor => editor.node.graph);
            active.forEach(editor => editor.lock());
            try { await Promise.all(active.map(editor => editor.flush())); return await original.apply(this, args); }
            finally { active.forEach(editor => editor.unlock()); }
        };
        app.graphToPrompt = prepare(app.graphToPrompt);
        const saves = new Set(["Comfy.SaveWorkflow", "Comfy.SaveWorkflowAs", "Comfy.ExportWorkflow", "Comfy.ExportWorkflowAPI"]);
        for (const command of app.extensionManager.command.commands) {
            if (saves.has(command.id)) command.function = prepare(command.function);
        }
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function(...args) { created?.apply(this, args); setupSpeedpaint(this); };
    },
});
