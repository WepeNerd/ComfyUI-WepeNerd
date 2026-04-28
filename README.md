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

## Nodes

### Resolution Suggest

Proportionally resizes a width/height while snapping to a divisor grid (32, 16, 8, or 64) — useful for preparing dimensions for latent images.

| Input | Description |
|---|---|
| `width` / `height` | Source resolution |
| `target` | Target size in pixels, or percentage when using Scale Factor mode |
| `resize_mode` | What the target controls: **Longest Side**, **Shortest Side**, **Width**, **Height**, or **Scale Factor** |
| `divisor` | Snap grid: 32 (default), 16, 8, or 64 |
| `snap_mode` | **round** (nearest), **floor** (down), or **ceil** (up) |

| Output | Type | Description |
|---|---|---|
| `width` | INT | Resized width (divisible by divisor) |
| `height` | INT | Resized height (divisible by divisor) |
| `original_width` | INT | Pass-through of input width |
| `original_height` | INT | Pass-through of input height |
| `scale_factor` | FLOAT | Actual scale applied |
| `info` | STRING | Human-readable summary |

**Example:** 1920×1080 with target 1024 (Longest Side, div 32) → **1024×576**

## License

MIT — see [LICENSE](LICENSE).
