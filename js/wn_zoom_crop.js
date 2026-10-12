import { app } from "../../scripts/app.js";

const GRID_SIZE = 140;
const GRID_TOP = 6;

function installGrid(node) {
    const widget = node.widgets?.find((item) => item.name === "position");
    if (!widget || widget.type === "wn_crop_position") return;
    widget.type = "wn_crop_position";
    widget.computeSize = () => [GRID_SIZE + 32, GRID_SIZE + 34];
    widget.serializeValue = function () { return this.value; };
    widget.draw = function (ctx, node, width, y) {
        const left = (width - GRID_SIZE) / 2;
        const top = y + GRID_TOP;
        const cell = GRID_SIZE / 5;
        this._grid = { left, top, cell };
        const selected = this.options.values.indexOf(this.value);
        ctx.save();
        for (let index = 0; index < 25; index++) {
            const x = left + (index % 5) * cell;
            const cy = top + Math.floor(index / 5) * cell;
            ctx.fillStyle = index === selected ? "#527b98" : "#292929";
            ctx.fillRect(x + 1, cy + 1, cell - 2, cell - 2);
            ctx.beginPath();
            ctx.arc(x + cell / 2, cy + cell / 2, index === selected ? 4 : 2, 0, Math.PI * 2);
            ctx.fillStyle = index === selected ? "#ffffff" : "#858585";
            ctx.fill();
        }
        ctx.fillStyle = "#bcbcbc";
        ctx.font = "12px Arial, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(`Keep: ${this.value}`, width / 2, top + GRID_SIZE + 14);
        ctx.restore();
    };
    widget.mouse = function (event, pos, node) {
        if (event.type !== "pointerdown" && event.type !== "mousedown") return false;
        if (event.button !== undefined && event.button !== 0) return false;
        const grid = this._grid;
        if (!grid) return false;
        const column = Math.floor((pos[0] - grid.left) / grid.cell);
        const row = Math.floor((pos[1] - grid.top) / grid.cell);
        if (column < 0 || column > 4 || row < 0 || row > 4) return false;
        const value = this.options.values[row * 5 + column];
        if (value !== this.value) {
            node.graph?.beforeChange();
            this.value = value;
            this.callback?.(value, app.canvas, node, pos, event);
            node.graph?.afterChange();
            node.setDirtyCanvas(true, true);
        }
        return true;
    };
    node.setSize(node.computeSize());
}

app.registerExtension({
    name: "WepeNerd.ZoomCrop",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "WN_ZoomCrop") return;
        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            installGrid(this);
            return result;
        };
    },
});
