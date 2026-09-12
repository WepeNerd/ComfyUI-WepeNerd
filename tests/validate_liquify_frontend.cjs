// Requires Playwright and Chrome. Point --url at a disposable ComfyUI instance.
// This opens a fresh browser session and queues small image-only test workflows.
const { chromium } = require('playwright');
const fs = require('node:fs');
const urlIndex = process.argv.indexOf('--url');
if (urlIndex < 0) throw Error('Usage: node tests/validate_liquify_frontend.cjs --url http://127.0.0.1:8199');

(async () => {
    const browser = await chromium.launch({ channel: 'chrome', headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
        const errors = []; page.on('pageerror', e => errors.push(e.message));
        await page.goto(process.argv[urlIndex + 1]);
        await page.waitForFunction(() => window.comfyAPI?.app?.app?.graph && LiteGraph.registered_node_types.WN_LiquifyImage);
        const results = await page.evaluate(async () => {
            const { app } = await import('/scripts/app.js'), { api } = await import('/scripts/api.js');
            const checks = [], pause = ms => new Promise(resolve => setTimeout(resolve, ms));
            const assert = (condition, message) => { if (!condition) throw Error(message); };
            async function wait(predicate) {
                const start = performance.now();
                while (!await predicate()) { if (performance.now() - start > 20000) throw Error('Timed out waiting for editor/queue'); await pause(25); }
            }
            const source = document.createElement('canvas'); source.width = 320; source.height = 200;
            const ctx = source.getContext('2d'), image = ctx.createImageData(320, 200);
            for (let y = 0; y < 200; y++) for (let x = 0; x < 320; x++) {
                const offset = (y * 320 + x) * 4;
                image.data.set([x % 256, y, ((x >> 4) + (y >> 4)) % 2 ? 230 : 30, 255], offset);
            }
            ctx.putImageData(image, 0, 0); const original = source.toDataURL();
            const file = new File([await (await fetch(original)).blob()], 'liquify-test.png', { type: 'image/png' });
            app.graph.clear(); app.canvas.ds.scale = 1; app.canvas.ds.offset = [0, 0];
            const create = () => { const node = LiteGraph.createNode('WN_LiquifyImage'); app.graph.add(node); node.pos = [100, 100]; return node; };
            const root = n => n.__liquify.root, canvas = n => root(n).querySelector('canvas');
            const widget = n => n.widgets.find(w => w.name === 'image_data');
            const data = n => JSON.parse(n.serialize().widgets_values[n.widgets.indexOf(widget(n))]);
            const pixels = n => canvas(n).toDataURL();
            const click = (n, text) => [...root(n).querySelectorAll('button')].find(b => b.textContent === text).click();
            function gesture(n, type, x, y, id = 7) {
                const c = canvas(n), rect = c.getBoundingClientRect();
                c.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'mouse', button: 0, buttons: type === 'pointerup' ? 0 : 1,
                    clientX: rect.left + rect.width * x, clientY: rect.top + rect.height * y, bubbles: true }));
            }
            // Synthetic pointer IDs have no native capture; native pointer capture is tested below.
            const capture = n => { canvas(n).setPointerCapture = () => {}; canvas(n).hasPointerCapture = () => false; };
            const node = create(); node.onDropFile(file); await wait(() => canvas(node).width === 320); await pause(50); capture(node);
            assert(data(node).source === original, 'Import did not retain original pixels');
            const before = pixels(node);
            gesture(node, 'pointerdown', .3, .5); gesture(node, 'pointermove', .5, .5);
            const mid = node.serialize(); assert(JSON.parse(mid.widgets_values[0]).strokes[0].points.length === 2, 'Save missed active stroke');
            const prompt = await app.graphToPrompt(); assert(JSON.parse(prompt.output[node.id].inputs.image_data).strokes.length === 1, 'Queue missed active stroke');
            gesture(node, 'pointermove', .6, .6); gesture(node, 'pointerup', .6, .6); await pause(30);
            const warped = pixels(node); assert(warped !== before, 'Brush did not change image');
            checks.push('Import, active-stroke Save/Queue, and continued painting');
            click(node, 'Undo'); assert(pixels(node) === before, 'Undo did not restore original');
            click(node, 'Redo'); assert(pixels(node) === warped, 'Redo differed');
            click(node, 'Reset'); assert(pixels(node) === before, 'Reset did not restore original');
            click(node, 'Undo'); assert(pixels(node) === warped, 'Undo Reset differed');
            checks.push('Undo, redo, and reversible Reset');
            const saved = node.serialize();
            const duplicate = LiteGraph.createNode('WN_LiquifyImage'); duplicate.configure({ ...saved, id: -1 }); app.graph.add(duplicate);
            await wait(() => canvas(duplicate).width === 320); await pause(30);
            assert(pixels(duplicate) === warped, 'Reload lost editable warp');
            click(duplicate, 'Undo'); assert(pixels(duplicate) === before && pixels(node) === warped, 'Duplicate shared editor state');
            checks.push('Saved editable history and independent duplicate');
            app.graph.remove(duplicate);
            const legacy = create(); widget(legacy).value = original; legacy.onConfigure({});
            assert(legacy.serialize().widgets_values[0] === original, 'Immediate save during decode lost original');
            await wait(() => canvas(legacy).width === 320); assert(pixels(legacy) === before, 'Legacy flattened pixels changed');
            app.graph.remove(legacy); checks.push('Legacy workflow pixels');
            const racing = create(), large = document.createElement('canvas'); large.width = 2000; large.height = 100;
            large.getContext('2d').fillRect(0, 0, 2000, 100);
            const largeFile = new File([await new Promise(resolve => large.toBlob(resolve))], 'large.png', { type: 'image/png' });
            const read = FileReader.prototype.readAsDataURL; let held;
            FileReader.prototype.readAsDataURL = function(file) { if (!held) held = [this, file]; else return read.call(this, file); };
            racing.onDropFile(file); racing.onDropFile(largeFile);
            await wait(() => canvas(racing).width === 1536);
            FileReader.prototype.readAsDataURL = read; read.call(...held); await pause(50);
            assert(data(racing).width === 2000, 'Late image load replaced newer selection');
            app.graph.remove(racing); checks.push('Full-resolution source with bounded preview and last import wins');
            gesture(node, 'pointerdown', .6, .3); gesture(node, 'pointermove', .7, .3); window.dispatchEvent(new Event('blur'));
            const afterBlur = data(node).strokes.length; gesture(node, 'pointermove', .8, .3);
            assert(data(node).strokes.length === afterBlur, 'Blur left gesture active');
            checks.push('Blur ends gesture');
            const preview = LiteGraph.createNode('PreviewImage'); app.graph.add(preview); preview.pos = [600, 100]; node.connect(0, preview, 0);
            async function queue() {
                const prompt = await app.graphToPrompt(), queued = await api.queuePrompt(0, prompt);
                let history;
                await wait(async () => { history = await (await api.fetchApi('/history/' + queued.prompt_id)).json(); return history[queued.prompt_id]; });
                assert(history[queued.prompt_id].status.status_str === 'success', JSON.stringify(history[queued.prompt_id].status));
                return history[queued.prompt_id];
            }
            const fileHistory = await queue(); assert(fileHistory.outputs[preview.id]?.images?.length, 'File warp produced no IMAGE output');
            const rendered = new Image(); rendered.src = api.apiURL('/view?' + new URLSearchParams(fileHistory.outputs[preview.id].images[0]));
            await rendered.decode(); const output = document.createElement('canvas'); output.width = rendered.width; output.height = rendered.height;
            const outputContext = output.getContext('2d'); outputContext.drawImage(rendered, 0, 0);
            const actual = outputContext.getImageData(0, 0, output.width, output.height).data;
            const expected = canvas(node).getContext('2d').getImageData(0, 0, output.width, output.height).data;
            const difference = actual.reduce((max, value, i) => Math.max(max, Math.abs(value - expected[i])), 0);
            assert(difference <= 2, 'Browser/backend warp mismatch: ' + difference);
            checks.push('Real backend queue from file');
            checks.push('Browser/backend pixel agreement within two 8-bit levels');
            const form = new FormData(); form.append('image', file);
            const uploaded = await (await api.fetchApi('/upload/image', { method: 'POST', body: form })).json();
            const input = LiteGraph.createNode('LoadImage'); app.graph.add(input); input.pos = [-260, 100];
            input.widgets.find(w => w.name === 'image').value = uploaded.name;
            input.connect(0, node, node.inputs.findIndex(i => i.name === 'image'));
            const connectedHistory = await queue(); await wait(() => data(node).source_mode === 'input');
            assert(data(node).strokes.length === afterBlur, 'Input preview discarded warps');
            assert(connectedHistory.outputs[node.id].liquify_source[0].width === 320, 'Input preview missing');
            await queue(); checks.push('IMAGE connection, source preview, and repeat queue');
            await app.refreshComboInNodes();
            const graph = app.graph.serialize(); await app.loadGraphData(graph); await pause(200);
            const restored = app.graph.getNodeById(node.id);
            assert(data(restored).source_mode === 'input' && data(restored).strokes.length === afterBlur, 'Connected workflow reload failed');
            await queue();
            checks.push('Connected workflow round-trip');
            const removed = create(), removedCanvas = canvas(removed); removed.onDropFile(file); app.graph.remove(removed); await pause(100);
            assert(removedCanvas.width === 0 && !removed.__liquify, 'Removed editor retained late import');
            checks.push('Removal during image load releases editor');
            window.liquifyTestNode = restored;
            return { checks, graph, source: original, backendOutput: fileHistory.outputs[preview.id].images[0] };
        });
        await page.waitForTimeout(150);
        await page.waitForFunction(() => [...document.querySelectorAll('.wn-liquify button')].some(b => b.textContent === 'Original' && !b.disabled));
        const beforeNative = await page.evaluate(() => JSON.parse(window.liquifyTestNode.serialize().widgets_values[0]).strokes.length);
        const bounds = await page.locator('.wn-liquify canvas').first().boundingBox();
        await page.mouse.move(bounds.x + bounds.width * .3, bounds.y + bounds.height * .3);
        await page.mouse.down(); await page.mouse.move(bounds.x + bounds.width + 30, bounds.y + bounds.height * .5, { steps: 12 });
        await page.mouse.up();
        const native = await page.evaluate(() => {
            const n = window.liquifyTestNode, saved = JSON.parse(n.serialize().widgets_values[0]);
            return { strokes: saved.strokes.length, points: saved.strokes.at(-1).points.length, captured: n.__liquify.root.querySelector('canvas').hasPointerCapture(1) };
        });
        if (native.strokes !== beforeNative + 1 || native.points < 2 || native.captured) throw Error('Native mouse release outside editor failed');
        results.checks.push('Native mouse drag and release outside canvas');
        await page.keyboard.press('Control+z');
        const undone = await page.evaluate(() => JSON.parse(window.liquifyTestNode.serialize().widgets_values[0]).strokes.length);
        if (undone !== native.strokes - 1) throw Error('Editor keyboard undo was intercepted by graph history');
        await page.keyboard.press('Control+Shift+z');
        const redone = await page.evaluate(() => JSON.parse(window.liquifyTestNode.serialize().widgets_values[0]).strokes.length);
        if (redone !== native.strokes) throw Error('Editor keyboard redo failed: ' + JSON.stringify(await page.evaluate(() => {
            const n = window.liquifyTestNode, d = JSON.parse(n.serialize().widgets_values[0]);
            return { strokes: d.strokes.length, redo: d.redo.length, active: document.activeElement?.outerHTML?.slice(0, 250), sameNode: window.comfyAPI.app.app.graph.getNodeById(n.id) === n };
        })));
        results.checks.push('Native keyboard undo and redo stay inside editor');
        const painted = await page.locator('.wn-liquify canvas').first().evaluate(c => c.toDataURL());
        const originalButton = await page.getByRole('button', { name: 'Original', exact: true }).boundingBox();
        await page.mouse.move(originalButton.x + originalButton.width / 2, originalButton.y + originalButton.height / 2);
        await page.mouse.down();
        if (await page.locator('.wn-liquify canvas').first().evaluate(c => c.toDataURL()) === painted) throw Error('Hold Original did not show source');
        await page.mouse.up();
        if (await page.locator('.wn-liquify canvas').first().evaluate(c => c.toDataURL()) !== painted) throw Error('Original comparison changed painting');
        results.checks.push('Hold Original compares without changing edits');
        for (const zoom of [1, .65]) {
            await page.evaluate(zoom => { window.comfyAPI.app.app.canvas.ds.scale = zoom; }, zoom);
            await page.waitForTimeout(50);
            const rect = await page.locator('.wn-liquify canvas').first().boundingBox();
            const x = rect.x + rect.width * .7, y = rect.y + rect.height * .7;
            await page.mouse.move(x, y); await page.waitForTimeout(40);
            const cursor = await page.locator('.wn-liquify canvas + div').first().boundingBox();
            if (Math.abs(cursor.x + cursor.width / 2 - x) > 2 || Math.abs(cursor.y + cursor.height / 2 - y) > 2) throw Error('Brush cursor is misaligned at zoom ' + zoom);
        }
        results.checks.push('Cursor alignment at full and reduced graph zoom');
        if (errors.length) throw Error('Browser errors: ' + errors.join('; '));
        console.log(JSON.stringify({ checks: results.checks }, null, 2));
        if (process.env.LIQUIFY_REPORT) fs.writeFileSync(process.env.LIQUIFY_REPORT, JSON.stringify(results, null, 2));
        if (process.env.LIQUIFY_SCREENSHOT) await page.screenshot({ path: process.env.LIQUIFY_SCREENSHOT });
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
