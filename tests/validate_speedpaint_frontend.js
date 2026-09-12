// Copy into js/ temporarily; open the isolated test server on port 8199.
// This runner only activates on port 8199 and replaces that test session's workflow.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

if (location.port === "8199") {
    app.registerExtension({
        name: "wepenerd.speedpaint.validation",
        setup() {
            const panel = document.createElement("div");
            panel.style.cssText = "position:fixed;right:8px;top:80px;width:360px;max-height:80vh;overflow:auto;background:#202126;color:#eee;padding:12px;z-index:10000;font:12px monospace;border:1px solid #888";
            const run = document.createElement("button"); run.textContent = "Run Speedpaint checks";
            const report = document.createElement("pre"); report.style.whiteSpace = "pre-wrap";
            panel.append(run, report); document.body.append(panel);
            run.onclick = async () => {
                run.disabled = true; report.textContent = "";
                const log = text => { report.textContent += text + "\n"; };
                const assert = (condition, message) => { if (!condition) throw new Error(message); };
                const wait = async condition => {
                    const deadline = performance.now() + 20000;
                    while (!condition()) { if (performance.now() > deadline) throw new Error("Timed out waiting for editor"); await new Promise(r => setTimeout(r, 30)); }
                };
                const widget = (n, name) => n.widgets.find(w => w.name === name);
                const root = n => widget(n, "speedpaint_editor").element;
                const drawing = n => root(n).querySelector("canvas");
                const documentValue = n => JSON.parse(widget(n, "document").value);
                const ready = n => wait(() => widget(n, "document").value && !root(n).querySelector(".sp-stage").classList.contains("sp-busy"));
                const flush = async n => { await ready(n); return app.graphToPrompt(); };
                const input = (n, selector, value, event = "input") => { const el = root(n).querySelector(selector); el.value = value; el.dispatchEvent(new Event(event, { bubbles: true })); };
                const click = async (n, label) => { root(n).querySelector(`[aria-label="${label}"]`).click(); await ready(n); };
                const pixels = n => drawing(n).toDataURL();
                const pixel = (n, x, y) => [...drawing(n).getContext("2d").getImageData(x, y, 1, 1).data];
                const paint = (n, samples, options = {}) => {
                    const c = drawing(n), rect = c.getBoundingClientRect();
                    // Synthetic pen tests cannot create native pointer capture. Real mouse capture is checked separately.
                    const capture = c.setPointerCapture; c.setPointerCapture = () => {};
                    const send = (type, p) => c.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 41, pointerType: options.pointerType || "mouse", pressure: p[2] ?? .5, buttons: type === "pointerup" ? 0 : 1, button: 0, clientX: rect.left + p[0]*rect.width/c.width, clientY: rect.top + p[1]*rect.height/c.height, ...options }));
                    try { send("pointerdown", samples[0]); for (const p of samples.slice(1)) send("pointermove", p); send("pointerup", samples.at(-1)); }
                    finally { c.setPointerCapture = capture; }
                };
                try {
                    await app.loadGraphData({ nodes: [], links: [], groups: [], version: .4 });
                    let node = LiteGraph.createNode("WN_Speedpaint"); app.graph.add(node); node.pos = [70, 90];
                    app.canvas.ds.scale = 1; app.canvas.ds.offset = [0, 0]; app.canvas.setDirty(true, true);
                    await ready(node);
                    widget(node, "width").value = 128; widget(node, "height").value = 128; widget(node, "width").callback(128);
                    await flush(node);
                    input(node, "input[type=color]", "#ffffff"); await click(node, "New");
                    assert(pixel(node, 50, 50).join() === "255,255,255,255", "Blank RGB");
                    assert(drawing(node).width === 128, "Local size"); log("PASS blank RGB and local dimensions");
                    input(node, "input[aria-label='Brush colour picker']", "#000000");
                    input(node, "input[type=range]", "20"); input(node, "input[type=number]", "30", "change");
                    assert(root(node).querySelector(".sp-size").textContent === "20px", "Size readout");
                    const unchanged = widget(node, "document").value;
                    input(node, "input[type=range]", "21");
                    assert(widget(node, "document").value === unchanged, "Brush setting dirtied image");
                    input(node, "input[type=range]", "20"); log("PASS live slider without changing output");
                    paint(node, [[20, 64], [108, 64]]);
                    const sparse = pixel(node, 64, 64); const strokePixels = pixels(node);
                    assert(sparse[0] >= 178 && sparse[0] <= 179, "Stroke opacity or continuity");
                    await click(node, "Undo · Ctrl/Cmd+Z");
                    paint(node, Array.from({length: 89}, (_, i) => [20+i, 64]));
                    assert(pixel(node, 64, 64).join() === sparse.join(), "Event-density opacity build-up");
                    paint(node, [[20, 64], [108, 64]]);
                    assert(pixel(node, 64, 64)[0] < sparse[0], "Second stroke did not build opacity"); log("PASS continuous strokes and per-stroke opacity");
                    const twice = pixels(node);
                    root(node).querySelector(".sp-shortcuts").dispatchEvent(new KeyboardEvent("keydown", {key: "z", ctrlKey: true, bubbles: true, cancelable: true}));
                    await ready(node); await new Promise(r => requestAnimationFrame(r));
                    assert(app.graph.getNodeById(node.id) === node && pixel(node, 64, 64).join() === sparse.join(), "Keyboard undo escaped to graph");
                    root(node).querySelector(".sp-shortcuts").dispatchEvent(new KeyboardEvent("keydown", {key: "z", ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true}));
                    await ready(node); assert(pixels(node) === twice, "Keyboard redo failed"); log("PASS keyboard undo/redo remains per node");
                    await click(node, "New"); await click(node, "Square brush");
                    input(node, "input[type=number]", "100", "change");
                    paint(node, [[20, 20], [108, 108]]);
                    assert(pixel(node, 64, 64)[0] === 0, "Square gaps");
                    assert(pixel(node, 13, 13)[0] === 0, "Square corners"); log("PASS square brush continuity");
                    await click(node, "New"); await click(node, "Round brush");
                    input(node, "input[type=range]", "40");
                    paint(node, [[64, 64, .1]], { pointerType: "pen" });
                    assert(pixel(node, 72, 64)[0] === 255, "Pen pressure did not shrink size");
                    await click(node, "New"); paint(node, [[64, 64, .5]]);
                    assert(pixel(node, 80, 64)[0] === 0, "Mouse did not use full size");
                    await click(node, "New"); await click(node, "Pen pressure controls size");
                    paint(node, [[64, 64, .1]], { pointerType: "pen" });
                    assert(pixel(node, 80, 64)[0] === 0, "Pressure off did not use full size"); log("PASS synthetic pressure and mouse fallback");
                    const beforeSample = pixels(node);
                    paint(node, [[0, 0]], { altKey: true });
                    assert(pixels(node) === beforeSample, "Eyedropper painted"); log("PASS Alt eyedropper");
                    await flush(node);
                    const prior = pixels(node);
                    widget(node, "width").value = 256; widget(node, "width").callback(256); await flush(node);
                    assert(drawing(node).width === 256, "Resize failed");
                    await click(node, "Undo · Ctrl/Cmd+Z");
                    assert(drawing(node).width === 128 && pixels(node) === prior, "Resize undo lost pixels/dimensions"); log("PASS painted resize and full undo");
                    const queued = await flush(node), queuedDocument = queued.output[node.id].inputs.document;
                    assert(!JSON.parse(queuedDocument).inline && JSON.parse(queuedDocument).asset, "Queue did not flush PNG");
                    const firstAsset = documentValue(node).asset;
                    const saved = app.graph.serialize();
                    await app.loadGraphData(saved); node = app.graph._nodes.find(n => n.type === "WN_Speedpaint"); await ready(node);
                    assert(pixels(node) === prior, "Workflow restore pixels");
                    const copied = node.clone(); copied.pos = [540, 90]; app.graph.add(copied); await ready(copied);
                    input(copied, "input[aria-label='Brush colour picker']", "#ff0000"); paint(copied, [[20, 20]]); await flush(copied);
                    assert(documentValue(node).asset === firstAsset && documentValue(copied).asset !== firstAsset, "Clone shared mutation");
                    app.graph.remove(copied); log("PASS queue flush, workflow restoration and clone isolation");
                    // Real backend INT output, unlike frontend-only PrimitiveNode.
                    const resolution = LiteGraph.createNode("WN_ResolutionSuggest"); app.graph.add(resolution); resolution.pos = [560, 80];
                    resolution.connect(0, node, node.inputs.findIndex(i => i.name === "width"));
                    await ready(node); const widthOnly = await flush(node);
                    assert(Array.isArray(widthOnly.output[node.id].inputs.width) && widthOnly.output[node.id].inputs.height === 128, "Width link precedence");
                    node.disconnectInput(node.inputs.findIndex(i => i.name === "width")); await ready(node);
                    resolution.connect(1, node, node.inputs.findIndex(i => i.name === "height")); await ready(node);
                    const heightOnly = await flush(node);
                    assert(heightOnly.output[node.id].inputs.width === 128 && Array.isArray(heightOnly.output[node.id].inputs.height), "Height link precedence");
                    resolution.connect(0, node, node.inputs.findIndex(i => i.name === "width")); await ready(node);
                    const both = await flush(node);
                    assert(Array.isArray(both.output[node.id].inputs.width) && Array.isArray(both.output[node.id].inputs.height), "Both links");
                    node.disconnectInput(node.inputs.findIndex(i => i.name === "width")); node.disconnectInput(node.inputs.findIndex(i => i.name === "height")); await ready(node);
                    assert(widget(node, "width").value === 128 && widget(node, "height").value === 128, "Disconnect defaults");
                    app.graph.remove(resolution); log("PASS independent dimension links and disconnect defaults");
                    const revision = widget(node, "document").value;
                    api.dispatchEvent(new CustomEvent("execution_start", { detail: { prompt_id: "test-current" } }));
                    const prepared = await (await api.fetchApi("/wepenerd/speedpaint/prepare", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({document: documentValue(node), width: 256, height: 128})})).json();
                    const result = {document: revision, asset: prepared.asset, width: 256, height: 128};
                    api.dispatchEvent(new CustomEvent("executed", { detail: {node: String(node.id), prompt_id: "test-current", output: {speedpaint: [result]}}}));
                    await wait(() => drawing(node).width === 256);
                    assert(widget(node, "document").value === revision, "Runtime resize replaced committed source");
                    input(node, "input[aria-label='Brush colour picker']", "#ff0000"); paint(node, [[64, 64]]);
                    assert(documentValue(node).width === 256, "Painting did not adopt runtime dimensions");
                    const latest = pixels(node);
                    api.dispatchEvent(new CustomEvent("executed", {detail: {node: String(node.id), prompt_id: "test-current", output: {speedpaint: [result]}}}));
                    await new Promise(r => setTimeout(r, 150));
                    assert(pixels(node) === latest, "Stale result replaced painting"); log("PASS runtime resize derivation, adoption and stale-result guard");
                    api.dispatchEvent(new CustomEvent("executing", {detail: null}));
                    const fetch = api.fetchApi;
                    try {
                        api.fetchApi = async function(path, ...args) {
                            if (path === "/wepenerd/speedpaint/commit") return new Response(JSON.stringify({error: "Simulated disk failure"}), {status: 400});
                            return fetch.call(this, path, ...args);
                        };
                        paint(node, [[90, 90]]); const unsaved = pixels(node);
                        let rejected = false;
                        try { await app.graphToPrompt(); } catch { rejected = true; }
                        assert(rejected && pixels(node) === unsaved, "Failed save queued stale pixels or lost painting");
                    } finally { api.fetchApi = fetch; }
                    await flush(node); log("PASS save failure blocks queue and retains artwork");
                    const imported = document.createElement("canvas"); imported.width = 320; imported.height = 128;
                    const importedCtx = imported.getContext("2d"); importedCtx.fillStyle = "#2a80b0"; importedCtx.fillRect(0, 0, 320, 128);
                    importedCtx.fillStyle = "#ffba40"; importedCtx.fillRect(0, 0, 100, 128);
                    const blob = await new Promise(r => imported.toBlob(r));
                    const transfer = new DataTransfer(); transfer.items.add(new File([blob], "test-import.png", {type: "image/png"}));
                    const fileInput = root(node).querySelector("input[type=file]"); fileInput.files = transfer.files; fileInput.dispatchEvent(new Event("change"));
                    const immediate = await app.graphToPrompt();
                    assert(documentValue(node).source && !documentValue(node).painted && drawing(node).width === 128, "Import ignored local dimensions");
                    assert(JSON.parse(immediate.output[node.id].inputs.document).asset === documentValue(node).asset, "Immediate queue used pre-import document");
                    const importedPixels = pixels(node);
                    paint(node, [[64, 64], [100, 64]], {shiftKey: true}); await flush(node);
                    assert(documentValue(node).crop[0] !== .5 && pixels(node) !== importedPixels, "Shift crop did not move source");
                    await click(node, "Undo · Ctrl/Cmd+Z"); assert(pixels(node) === importedPixels, "Crop undo failed");
                    log("PASS import, immediate queue, reposition and crop undo");
                    const beforeResize = pixels(node);
                    try {
                        api.fetchApi = async function(path, ...args) {
                            if (path === "/wepenerd/speedpaint/prepare") await new Promise(r => setTimeout(r, 150));
                            return fetch.call(this, path, ...args);
                        };
                        widget(node, "width").value = 256; widget(node, "width").callback(256);
                        const prompt = await app.graphToPrompt();
                        assert(JSON.parse(prompt.output[node.id].inputs.document).width === 256, "Queue raced pending resize");
                    } finally { api.fetchApi = fetch; }
                    await click(node, "Undo · Ctrl/Cmd+Z"); assert(pixels(node) === beforeResize, "Delayed resize undo");
                    log("PASS immediate queue waits for delayed resize");
                    await flush(node);
                    node.setSize([450, 610]); app.canvas.setDirty(true, true);
                    log("ALL CHECKS PASSED · " + navigator.userAgent);
                } catch (error) { log("FAIL " + error.stack); }
                finally { run.disabled = false; }
            };
        },
    });
}
