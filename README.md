# ComfyUI-WepeNerd

Custom node pack for [ComfyUI](https://github.com/comfyanonymous/ComfyUI) by **WepeNerd**.

## Installation

### Via ComfyUI Manager
Search for **WepeNerd** in ComfyUI Manager and click Install.

### Manual
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/WepeNerd/ComfyUI-WepeNerd.git
```
Restart ComfyUI. Nodes appear under the **WepeNerd/Resolution** category.

---

## Nodes

### Drag Resolution

An interactive visual resolution picker. Drag a box to set your output dimensions. Values snap to the chosen divisor grid in real time.

**Features:**
- Drag side handles to change one axis at a time, updating the aspect ratio as the pixel size changes
- Drag corner handles to scale the resolution while preserving the current aspect ratio
- Choose an aspect ratio preset (16:9, 4:3, 1:1, 9:16, and more) to reshape the box before dragging
- Use the width/height input arrows to step by the current divisor value
- Real-time dimension readout with ratio label on the box
- Divisor snapping (32, 16, 8, 64) keeps every output cleanly divisible
- Grid overlay shows divisor increments

| Input | Description |
|---|---|
| `width` / `height` | Resolution (also set by dragging the box); arrow buttons step by the selected divisor |
| `aspect_ratio` | Preset ratio to apply, or Free for the current/custom ratio |
| `divisor` | Snap grid: 32 (default), 16, 8, or 64 |

| Output | Type | Description |
|---|---|---|
| `width` | INT | Final width (divisible by divisor) |
| `height` | INT | Final height (divisible by divisor) |
| `aspect_ratio` | STRING | Simplified ratio string (e.g. "16:9") |
| `info` | STRING | Human-readable summary |

---

## Workflow Compatibility

Node IDs, widget names, output names, and the `WepeNerd/Resolution` category are unchanged. Existing workflows should continue to load. The frontend extension now lives in `./js/`, matching the exported `WEB_DIRECTORY`.

After updating, restart ComfyUI, hard refresh the browser, create a Drag Resolution node, drag side and corner handles, and check the browser dev console for JavaScript errors.

---

### Resolution Suggest

Takes a source width/height and proportionally resizes to a target, snapped to a divisor grid. Useful for preparing dimensions for models that need specific multiples.

| Input | Description |
|---|---|
| `width` / `height` | Source resolution |
| `target` | Target size in pixels, or percentage for Scale Factor mode |
| `resize_mode` | Longest Side, Shortest Side, Width, Height, or Scale Factor |
| `divisor` | Snap grid: 32, 16, 8, or 64 |
| `snap_mode` | round, floor, or ceil |

| Output | Type | Description |
|---|---|---|
| `width` | INT | Resized width |
| `height` | INT | Resized height |
| `original_width` | INT | Pass-through of input width |
| `original_height` | INT | Pass-through of input height |
| `scale_factor` | FLOAT | Actual scale applied |
| `aspect_ratio` | STRING | Simplified ratio string |
| `info` | STRING | Human-readable summary |

---

## License

MIT — see [LICENSE](LICENSE).
