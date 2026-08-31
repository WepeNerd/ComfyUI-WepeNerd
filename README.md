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

Restart ComfyUI. Nodes appear under the **WepeNerd/Resolution**, **WepeNerd/3D**, **WepeNerd/Image**, **WepeNerd/Video**, and **WepeNerd/Local AI** categories.

---

## Nodes

### Local AI

**Category:** `WepeNerd/Local AI`

The normal workflow has four simple nodes:

```text
Local AI Model
    model: Muse / Qwen GGUF
        |
        +--> Prompt Enhancer
        |      skill: H3 or Krea 2
        |
        +--> Image Captioner
        |
        +--> Video Captioner
```

`Local AI Model` uses safe defaults, finds `llama-server` automatically, and releases its external CUDA allocation after each generation. `Prompt Enhancer` includes researched, local H3 and Krea 2 skills. Image batches produce one caption per image. Video captioning automatically uses native video only when the backend explicitly reports support; otherwise it samples chronological frames with memory-safe seeking.

Put model files here (subfolders are supported):

```text
ComfyUI/models/LLM/
```

For example, the model and projector in this setup are:

```text
Huihui-Qwen3.8-27B-abliterated-Q4_K.gguf
mmproj-model-bf16.gguf
```

The nodes do not download or bundle llama.cpp. Install a recent `llama-server` build separately, then use one of these options:

- put `llama-server.exe` on `PATH`;
- set the `LLAMA_SERVER_PATH` environment variable;
- extract the Windows build and CUDA runtime DLLs together under `C:\llamacpp\`;
- copy the executable and its required DLLs to `ComfyUI-WepeNerd/bin/`; or
- enter the full executable path in `Local AI Model (Advanced)`.

Select `LLM/Huihui-Qwen3.8-27B-abliterated-Q4_K.gguf` as the model. The defaults request 24 GB of free VRAM, offload all model layers supported by the backend (`gpu_layers = -1`), use an 8192-token context, and release the server after generation.

For image or video captioning, also select `LLM/mmproj-model-bf16.gguf`. Projectors are architecture/model-specific; discovery does not imply compatibility. Images are resized without upscaling and encoded as JPEG at quality 90. An image batch is processed through one server acquisition and returns one caption per image.

Available nodes:

| Node | Purpose |
|---|---|
| `Local AI Model` | Select a model and optional projector with safe defaults |
| `Prompt Enhancer` | Rewrite a prompt using the bundled H3, Krea 2, or a custom skill |
| `Image Captioner` | Caption every image in a ComfyUI `IMAGE` batch |
| `Video Captioner` | Automatically caption native video or sampled chronological frames |

Prompt enhancement and caption nodes set `reasoning_effort` to `none`. Returned `<think>...</think>` blocks are removed, and hidden `reasoning_content` is never returned as a prompt or caption.

Video auto mode checks llama-server `/props`: it uses typed native `input_video` only when video support is explicit, otherwise it sends timestamped JPEG frames. Missing metadata is treated as unknown and falls back conservatively. File-backed clips use PyAV seek sampling, so memory scales with selected frames rather than total clip length. Audio and dialogue are not inferred.

#### Local AI / Advanced

**Category:** `WepeNerd/Local AI/Advanced`

Use the advanced nodes for raw generation, a custom context size, GPU layers, KV cache overrides, Flash Attention overrides, keep-alive, a custom server executable, secondary-GPU selection, native/sampled video controls, status, or manual unloading.

| Node | Purpose |
|---|---|
| `Local AI Model (Advanced)` | Full model, server, memory, and lifecycle configuration |
| `Local AI Generate` | General text generation with optional image input |
| `Prompt Enhancer (Advanced)` | Legacy styles and sampler controls, including H3 and Krea 2 |
| `Image Captioner (Advanced)` | Caption cleanup, encoding, and sampler controls |
| `Video Captioner (Advanced)` | Native/sampled modes and sampling controls |
| `Local AI Status` | Report the managed server and advertised modalities |
| `Unload Local AI Model` | Stop a resident keep-alive server |

`release_after_generate = false` is an advanced speed option for consecutive calls. `keep_alive_seconds` can release an idle server automatically; zero means manual indefinite keep-alive. While resident, external llama.cpp VRAM is invisible to ComfyUI, so run `Unload Local AI Model` before returning to a heavy diffusion or video branch and create an actual STRING dependency edge when sequencing matters.

Advanced config includes Flash Attention, F16/Q8 KV caches, vision-token bounds, and child-only `CUDA_VISIBLE_DEVICES`. For a dedicated secondary GPU, set `cuda_visible_devices` and choose `comfy_vram_handoff = never`; this does not modify ComfyUI's own environment. Q8 KV caches save memory but can change speed or quality slightly.

The backend expects a current llama.cpp build with `--jinja`, `/health`, streaming chat completions, `reasoning_effort`, and multimodal `image_url` support. `/props` enriches identity/capability checks but incomplete metadata is tolerated. Native video additionally requires typed `input_video`; auto mode uses it only when `/props` explicitly advertises video support.

Troubleshooting:

- **llama-server was not found:** set the executable path as described above. A `.gguf` file cannot run by itself.
- **Startup timeout or early exit:** inspect the ComfyUI console; the error includes the bounded tail of llama-server output.
- **CUDA out of memory:** enable `aggressive_vram_handoff`, reduce context size, or reduce GPU layers.
- **Image request rejected:** confirm the model is vision-capable, the projector belongs to the exact model, and the llama.cpp build is recent enough.
- **Native video unavailable:** use `sampled_frames`; native support depends on both the model/projector and the llama.cpp build.

---

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
- Enter a Target MP value to generate a nearby divisor-aligned resolution at the selected aspect
- Use the width/height input arrows to step by the current divisor value
- Real-time dimension, megapixel, target-ratio, and actual-ratio readout on the box
- Divisor snapping (32, 16, 8, 64) keeps every output cleanly divisible
- Grid overlay shows divisor increments

| Input | Description |
|---|---|
| `width` / `height` | Resolution (also set by dragging the box); arrow buttons step by the selected divisor |
| `aspect_ratio` | Preset ratio to apply, or Free for the current/custom ratio |
| `divisor` | Snap grid: 32 (default), 16, 8, or 64 |
| `target_mp` | Resolution helper that scales width/height toward a requested megapixel area |

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

## Changelog

### 2026-08-20 — Local AI simplified workflow

- Add the clean `Local AI Model`, `Prompt Enhancer`, `Image Captioner`, and `Video Captioner` workflow while preserving every existing GGUF node ID and socket type.
- Bundle local MiniMax H3 and Krea 2 prompt skills with safe, cached loading.
- Make video auto mode capability-aware and lazy, with PyAV seek sampling for long file-backed clips.
- Treat incomplete `/props` metadata as unknown, preserve healthy keep-alive servers on cancellation, and omit optional llama.cpp flags at their defaults.

### 2026-08-20 — GGUF LLM/VLM hardening

- Prevent hidden reasoning from becoming prompt or caption output.
- Add streaming cancellation, verified server identity, safer process cleanup, and optional timed keep-alive.
- Add full image-batch captioning, prompt/caption presets, optimized media encoding, modern samplers, and secondary-GPU controls.
- Add `GGUF Caption Video` with native-video capability detection and timestamped sampled-frame fallback.
- Register `models/LLM` through Comfy folder paths with extra-path support and cached discovery.

---

## License

MIT — see [LICENSE](LICENSE).
