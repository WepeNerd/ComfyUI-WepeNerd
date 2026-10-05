import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../js/wn_masked_lora.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");

function load() {
    const extensions = [];
    const context = vm.createContext({
        document: { createElement: () => ({ style: {} }), head: { append() {} } },
        app: { registerExtension: extension => extensions.push(extension) }, api: {},
    });
    vm.runInContext(source, context);
    return { context, extension: extensions[0] };
}

test("Qwen Edit Mask: editor size note matches the Python / TextEncodeQwenImage21 resize rule", () => {
    const { context } = load();
    // [width, height, resolution, expected width, expected height] from qwen_edit_mask_node.qwen_size().
    const cases = [[1000, 700, 1024, 1216, 864], [1040, 720, 0, 1024, 704], [1920, 1080, 1024, 1376, 768],
        [1536, 1024, 1024, 1248, 832], [17, 3000, 512, 32, 6816], [3, 5, 0, 32, 32],
        [4000, 3000, 2048, 2368, 1760], [1080, 1920, 1328, 992, 1760]];
    for (const [width, height, resolution, w, h] of cases) {
        assert.deepEqual([...context.qwenSize(width, height, resolution)], [w, h], `${width}×${height} @ ${resolution}`);
    }
});

test("Qwen Edit Mask: uses the shared standalone mask editor", async () => {
    const { extension } = load();
    for (const name of ["WN_PaintMask", "WN_QwenEditMask", "WepeNerdLoadLoraMasked"]) {
        const nodeType = { prototype: {} };
        await extension.beforeRegisterNodeDef(nodeType, { name });
        assert.equal(typeof nodeType.prototype.onNodeCreated, "function", name);
    }
    const other = { prototype: {} };
    await extension.beforeRegisterNodeDef(other, { name: "KSampler" });
    assert.equal(other.prototype.onNodeCreated, undefined);
});
