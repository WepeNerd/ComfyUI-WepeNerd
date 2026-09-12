# Node guide

## Drag Resolution


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

## Resolution Suggest


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



## Resize Image Megapixels


**Category:** `WepeNerd/Image` Â· **Input/Output:** `IMAGE`.

Resize an image or batch to a target megapixel count (1 MP = 1,000,000 pixels).
The node calculates the size at the original aspect ratio, then rounds each
dimension to the nearest multiple of **2, 4, 8, 16, 32, or 64** (minimum one
multiple). Actual megapixels may be slightly above or below the target, with
small aspect-ratio changes from rounding; the whole image is retained without
cropping. Supports upscaling and downscaling with ComfyUI's **lanczos** (default),
**bicubic**, **bilinear**, **nearest-exact**, and **area** interpolation.


## Slider


A standardized semantic `-1 â†’ 0 â†’ +1` control that outputs a normal ComfyUI
`FLOAT`. Connect its `strength` output to `strength_model`, `strength_clip`, or
any other compatible FLOAT input. It maps LOW, CENTER, and HIGH independently,
so positive-only, asymmetric, and reversed ranges work without changing the
everyday slider interface.

- Name each control with `Label` (for example, Realism, Age, or Detail).
- Set LOW, CENTER, HIGH, and Curve for the control's semantic range.
- Use **Hide Calibration** for the compact everyday view; right-click the node
  and choose **Show Calibration** to reopen it. The visibility state is saved.
- Drag near the center to snap to exactly `0`; hold Shift to bypass snapping.
- Double-click the track, or use **Reset Slider to Center** in the node context
  menu, to reset the normalized position to `0`.

For a positive-only `0 â†’ 3` range, use LOW `0`, CENTER `1.5`, and HIGH `3`,
so both halves of the control remain useful. The Slider only produces a FLOAT;
it does not select or load LoRAs.


## Speedpaint


**Category:** `WepeNerd/Image` Â· **Output:** `IMAGE`.

Sketch on a solid background with **New**, or **Load** / drop a PNG, JPEG or WebP
and paint over it. Width and height accept INT connections and local values
(64â€“4096 pixels, step 1). Imports use an oriented, colour-managed, centered Lanczos
crop at that exact size. Shift-drag before painting to reposition the crop.

The compact toolbar has round/square brushes, colour with a hex picker, a live
pixel-size slider, stroke opacity, pen pressure to size, and undo. The size slider
gives small brushes more travel; its arrow keys and `[` / `]` adjust by one pixel.
Alt-click samples colour; Ctrl/Cmd+Z undoes a stroke or canvas operation;
Ctrl/Cmd+Shift+Z redoes it. Mouse strokes use full brush size. Resize the node to
enlarge its canvas display without changing image resolution.

Resizing painted artwork transforms the whole composition with Lanczos. **New**
and image import read current dimensions connected from Drag Resolution or INT
primitives, including through reroutes. Other computed dimensions resolve during
execution; the returned preview retains the committed source until you paint on
it. Undo never changes an upstream resolution node.
Connect `image` to **VAE Encode**, then use your existing sampler workflow.

Paintings and original imports use immutable assets in
`ComfyUI/input/wepenerd_speedpaint`. Copy that folder along with workflows when
moving machines. Queueing and standard Save/Export commands wait for pending
preparation and asset writes; synchronous copies also retain a recovery PNG if a
write is pending. Undo history is limited to 30 operations / 128 MiB and does not
persist across reloads. Background colour changes apply to the next New/import.

Validated with ComfyUI 0.34.0 / frontend 1.51.10, browser integration checks and a
real VAE Encode/Decode execution. Physical tablet pressure has not been tested.


## Liquify Image

**Category:** `WepeNerd/Image` · **Outputs:** `IMAGE`, `MASK`.

Load or drop a PNG, JPEG or WebP, then drag the push brush to reshape it. The
original image stays intact: Undo/Redo removes or restores strokes, Reset removes
all warps, and holding Original lets you compare. Ctrl/Cmd+Z and
Ctrl/Cmd+Shift+Z work while the editor is focused. Reset can be undone during the
current editing session.

The optional **image** connection accepts a normal IMAGE or batch. Connect it and
Queue once to load the first image as the editor preview; edit, then Queue again
to apply the warp. Keep a downstream Preview Image or other output node connected
so ComfyUI executes this path. Connected images take precedence over file imports.
The same proportional warp applies to every batch image, including new upstream
images on later runs. Disconnect IMAGE before loading a file in the editor.

Output retains the original dimensions, up to 64 megapixels per image. Only the
interactive preview is reduced to a 1536-pixel maximum edge. The backend replays
the strokes against the original pixels at full resolution; it does not enlarge
the preview. Resizing the node enlarges its display. Fine details and colours in
the reduced preview can differ slightly from the full-resolution output.

Saved workflows contain the original imported file and editable stroke/redo
history. They remain portable without a separate asset folder. For IMAGE inputs,
the saved workflow contains a preview and stroke history; keep its upstream
image source available. Old flattened paintings load unchanged and become the
source for subsequent editable strokes. The original edits in those older
paintings cannot be reconstructed.

The MASK output preserves the node's existing **alpha coverage** convention:
1 means opaque and 0 means transparent; RGB inputs produce an all-one mask.
Invert it when an inpainting node expects 1 to mean the transparent region.
Alpha and colour are warped together. This is an image warp, not a mask painter.

Imports are limited to 64 MiB. Large embedded originals can produce large workflow
files. History supports 2000 strokes / 100,000 points; longer edits can be split
across connected Liquify nodes. File imports support EXIF orientation and embedded
colour profiles. Queued rendering checks for cancellation between image strips.

Open the [example workflow](../examples/liquify.json) for a portable image and a
saved editable warp connected to Preview Image.


## Load LoRA Masked


**Category:** `WepeNerd/Loaders` Â· **Model:** native floating-point or INT8 ConvRot Krea2.

Use ComfyUI's **Load Diffusion Model** loader, including for native INT8 ConvRot
checkpoints. The base layer retains its quantized forward calculation; the masked
LoRA/LoKr contribution runs separately in the activation dtype. The masked node does
not expand the base weights to floating point. Ordinary global LoRA patches still
follow ComfyUI's own weight patching and requantization behavior.

Connect MODEL, choose an installed LoRA and set strength (negative values are supported).
Open **Edit mask** and paint with the brush, rectangle or eraser. Empty masks have no
effect. The magenta overlay is a fixed 45% display preview; painted interiors apply
the full selected strength. Undo restores a whole gesture, image replacement or Clear.

Drop/open an image to use its exact oriented dimensions, or paint on the default
1024 Ã— 1024 blank canvas. Masks map proportionally to the sampled image grid; use a
reference with the intended aspect ratio for aligned regions. The editor follows the
compact icon-toolbar layout, with explicit image loading and a removable reference.
Right-click the node for **Open reference imageâ€¦** while IMAGE is connected.

**Load input** uses an available upstream preview. Otherwise it offers **Run upstream**,
which queues only the connected IMAGE ancestors and a private snapshot sink. It uses
the first batch image and never queues downstream generation. Connecting or removing
a wire does not replace the reference. Different-size replacements require Replace /
Cancel when painted. Same-size replacements retain the mask.

Mask PNGs, reference PNGs, geometry, brush size and folded state are saved in the
workflow; image data is embedded so exporting the workflow does not lose those assets.
LoRA/model files must still be installed on the destination machine. Uploaded references
and executed masks also use ComfyUI's input assets. Duplicates have independent editors.

Chained masked nodes add independent spatial contributions and preserve ordinary
global LoRA weight patches. Supported adapters are linear LoRA and full/factored LoKr,
including alpha/rank scaling. DoRA, convolution/Tucker, reshape and other adapter formats
are rejected on spatial layers. Text, timestep, modulation and normalization layers are
omitted and diagnosed in logs. Native still-image reference layouts `index` and
`index_timestep_zero` are supported; reference tokens themselves are not adapted.

A full mask need not equal a global loader because of the omitted layers. Attention and
denoising can spread indirect effects beyond the painted area; use final compositing
when exact pixel preservation is needed. Custom INT8 loaders, GGUF, FP8, NVFP4, temporal inputs and patches
that rearrange input tokens are outside v1 support. Real-model fidelity, generation
timing and peak VRAM have not been benchmarked.
