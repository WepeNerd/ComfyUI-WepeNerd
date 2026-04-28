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
Restart ComfyUI. Nodes appear under the **WepeNerd** category.

---

## Nodes

### Drag Resolution ✨ (NEW)

An interactive visual resolution picker. Drag a box to set your output dimensions — the box snaps to your chosen aspect ratio and divisor grid in real time.

**Features:**
- Drag corners, edges, or the whole box to set width/height
- Aspect ratio locking (16:9, 4:3, 1:1, 9:16, and more — or Free)
- Real-time dimension readout with ratio label on the box
- Divisor snapping (32, 16, 8, 64) — every output is always cleanly divisible
- Grid overlay shows divisor increments

| Input | Description |
|---|---|
| `width` / `height` | Resolution (also set by dragging the box) |
| `aspect_ratio` | Lock to a ratio or set Free for unconstrained |
| `divisor` | Snap grid: 32 (default), 16, 8, or 64 |

| Output | Type | Description |
|---|---|---|
| `width` | INT | Final width (divisible by divisor) |
| `height` | INT | Final height (divisible by divisor) |
| `aspect_ratio` | STRING | Simplified ratio string (e.g. "16:9") |
| `info` | STRING | Human-readable summary |

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
