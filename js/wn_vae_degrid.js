// VAE DeGrid: show the measured grid strength under the node after each run.
import { app } from "../../scripts/app.js";

const NODE_NAME = "WN_VAEDeGrid";
const LINE_HEIGHT = 16;

function installReadout(node) {
    const state = { lines: ["Run once to measure the grid."] };
    const widget = {
        name: "degrid_status", type: "wn_degrid_status", value: "", serialize: false,
        options: { serialize: false },
        computeSize: (width) => [width ?? 300, LINE_HEIGHT * Math.max(1, state.rows ?? state.lines.length) + 8],
        draw(ctx, _node, width, y) {
            ctx.save();
            ctx.font = "11px Arial, sans-serif";
            ctx.textBaseline = "top";
            let row = 0;
            for (const [index, text] of state.lines.entries()) {
                ctx.fillStyle = index === 0 ? "#cfcfcf" : "#9a9a9a";
                let line = "";
                for (const word of String(text).split(" ")) {
                    if (line && ctx.measureText(`${line} ${word}`).width > width - 28) {
                        ctx.fillText(line, 14, y + 4 + LINE_HEIGHT * row++);
                        line = word;
                    } else line = line ? `${line} ${word}` : word;
                }
                if (line) ctx.fillText(line, 14, y + 4 + LINE_HEIGHT * row++);
            }
            ctx.restore();
            if (row !== state.rows) {
                state.rows = row;
                queueMicrotask(() => node.setSize?.([node.size[0], node.computeSize()[1]]));
            }
        },
        mouse: () => false,
    };
    node.addCustomWidget ? node.addCustomWidget(widget) : node.widgets?.push(widget);
    const onExecuted = node.onExecuted;
    node.onExecuted = function (message) {
        if (Array.isArray(message?.text) && message.text.length) state.lines = message.text;
        node.setDirtyCanvas?.(true, true);
        return onExecuted?.apply(this, arguments);
    };
}

app.registerExtension({
    name: "WepeNerd.VAEDeGrid",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            installReadout(this);
            return result;
        };
    },
});
