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
Install dependencies:

```bash
cd ComfyUI-WepeNerd
pip install -r requirements.txt
```

Restart ComfyUI. Nodes appear under the **WepeNerd/Resolution**, **WepeNerd/3D**, **WepeNerd/Image**, and **WepeNerd/Video** categories.

---

## Nodes

### Exact Video Frames/FPS (WepeNerd)

**Category:** `WepeNerd/Video`

Loads a video from ComfyUI's input folder, or accepts a file-backed `VIDEO` input, and writes a new video with an exact target frame count and FPS.

Because exact frame count/FPS changes require frame timing work, the node has two quality paths:

- `lossless exact (FFV1/MKV)` decodes and writes a lossless MKV. This is the default because it verifies exact frame count and FPS without adding lossy generation loss.
- `lossless exact (H.264 RGB/MP4)` writes a lossless H.264 MP4 for workflows that need MP4 output.
- `stream copy best effort (no re-encode)` copies compressed video packets without re-encoding. It verifies the requested frame count, but FPS metadata is best effort because many containers preserve source packet timing during stream copy.

Use `extension_mode` to choose what happens when the requested output is longer than the source at the requested FPS:

- `hold_last_frame` extends with the final frame.
- `loop_source` repeats the source video.

Audio is dropped by default. `copy_trim_audio` copies the source audio packets and trims them to the new video duration without re-encoding, when the source/container supports it.

| Input | Description |
|---|---|
| `video_file` | Video file from the ComfyUI input directory |
| `video` | Optional connected file-backed ComfyUI `VIDEO` input |
| `target_frame_count` | Exact number of output video frames |
| `target_fps` | Exact output frame rate for lossless exact modes |
| `quality_mode` | Lossless exact output or best-effort packet stream copy |
| `extension_mode` | Hold the final frame or loop the source when more frames are needed |
| `audio_mode` | Drop audio or copy/trim source audio packets |
| `filename_prefix` | Output path prefix under the ComfyUI output directory |

| Output | Type | Description |
|---|---|---|
| `video` | VIDEO | Generated video, ready for ComfyUI video nodes |
| `output_path` | STRING | Absolute path to the generated file |
| `info` | STRING | Source/output probe details and mode notes |

Requires FFmpeg and FFprobe on PATH.

---

### Liquify Image (WepeNerd)

**Category:** `WepeNerd/Image`

A self-contained browser liquify editor. Load or drag and drop an image directly inside the node, push-warp it with a brush, and output the latest warped result as a ComfyUI `IMAGE` plus an alpha-derived `MASK`.

Current v1 limitation: this node is self-loading only. It does not yet accept an upstream ComfyUI `IMAGE` input. Larger images are downscaled to the browser working cap defined by `MAX_DIM` in `js/wn_liquify.js`.

Known limitations:

- The edited PNG is stored as base64 in the workflow JSON, so very large saved workflows are possible.
- Reopening a workflow restores the last flattened warped image, not the original image plus editable displacement field.
- Upstream `IMAGE` input support is planned for a future version.

---

### Load OBJ

**Category:** `WepeNerd/3D`

Loads a Wavefront `.obj` model from a local path and produces a reusable `obj_model` connection for 3D placement. It also outputs a clay preview image and preview mask, so you can inspect the model before placing it over a background.

Use the `choose .obj to upload` button or drag and drop an `.obj` file onto the node. Uploaded OBJ files are saved under `ComfyUI/input/3d/`, and the node fills the path widget automatically.

| Output | Type | Description |
|---|---|---|
| `obj_model` | WN_OBJ3D | Connect this to `3D Product Placement` |
| `preview` | IMAGE | Clay preview render on a neutral background |
| `preview_mask` | MASK | Alpha mask for the preview render |

---

### 3D Product Placement

**Category:** `WepeNerd/3D`

An interactive 3D object placement node for creating guide composites. Connect a normal ComfyUI `Load Image` node as the background, connect `Load OBJ (WepeNerd)` as the object, position the object visually in a JavaScript viewport, and output a composited image with the untextured clay model over the background.

The node is designed for product/object placement guides, not final photoreal rendering.

The queued composite uses a hidden browser viewport capture when available, so the output should match what you see in the node. If the capture is unavailable, the node falls back to the server-side renderer.

**Features:**
- Use a connected `IMAGE` input as the background and final output size
- Use a connected `Load OBJ (WepeNerd)` node as the 3D model
- Preview the model as a grey untextured clay object
- Rotate, move, and scale the object interactively
- Adjust basic directional lighting
- Toggle a wireframe overlay for placement guides
- Output a composited `IMAGE` and an object `MASK`
- Store placement values as normal widgets so workflows save and reload

| Action | Result |
|---|---|
| Left drag | Rotate object |
| Shift + drag | Move object X/Y |
| Mouse wheel | Scale object |
| Right drag / Alt + drag | Adjust light direction |
| Double-click | Reset placement |

| Input | Description |
|---|---|
| `background_image` | Connected `IMAGE` used as the scene/background. Determines final output dimensions |
| `obj_model` | Connected `WN_OBJ3D` from `Load OBJ (WepeNerd)` |
| `x_offset` / `y_offset` / `z_offset` | Object position controls |
| `scale` | Object scale |
| `rotate_x` / `rotate_y` / `rotate_z` | Object rotation in degrees |
| `camera_zoom` | Orthographic camera zoom |
| `light_yaw` / `light_pitch` | Directional light position |
| `light_intensity` | Light strength |
| `wireframe_overlay` | Draw a dark wireframe over the clay object in the viewport and composite |
| `opacity` | Opacity of the clay object in the final composite |

| Output | Type | Description |
|---|---|---|
| `composite` | IMAGE | Background image with clay object composited over it |
| `object_mask` | MASK | Alpha mask of the rendered 3D object |

**Security note:** This first version is intended for local ComfyUI use. If you run ComfyUI with `--listen` or expose it to a network, restrict the OBJ preview route to safe folders before using absolute model paths. Good future-safe locations are `ComfyUI/input/3d/` and `ComfyUI/models/3d/`.

---

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
