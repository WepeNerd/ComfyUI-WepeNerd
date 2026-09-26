# Node guide

## Load Video (Upload)

Loads MP4, WebM, MKV, MOV, AVI, M4V, or GIF files from ComfyUI's input folder,
including subfolders. Uses PyAV (also supplied by current ComfyUI); VHS and a
separate FFmpeg executable are not required. Codec support depends on PyAV.

| Input | Description |
|---|---|
| `video` | Upload/select a video. GIF files can also be placed in the input folder and selected. |
| `frame_rate` | 0 keeps source frames. A positive FPS samples the video timeline, dropping or repeating frames without interpolation. |
| `frame_load_cap` | Maximum selected frames; 0 means all. Decoding stops at the cap. |
| `skip_first_frames` | Frames skipped **after** rate conversion. |
| `select_every_nth` | Keep every nth frame after skipping; 1 keeps all. |
| `format` | VHS model dimension and frame-count constraints; default `None`. Does not change the FPS setting. |
| `custom_width` / `custom_height` | 0/0 preserves source size. Set one to preserve aspect ratio; set both to resize to that shape. Dimensions then round to the preset's nearest multiple. |
| `load_audio` | Enabled by default. Decode the selected clip's audio; disable for image-only workflows. |

The source-video preview autoplays muted and loops when a video is selected or
the workflow is reopened. Use its controls to pause or unmute. It previews the
original file, not the sampled output batch; browser codec support determines
which files can play inline.

Processing order: frame rate → skip → every nth → cap → format trimming.
For example, rate 10, skip 2, nth 2, and cap 3 selects resampled frames 2, 4,
and 6, with an output rate of 5 FPS. Positive rates use FFmpeg's standard FPS
filter with nearest timestamp rounding; source-frame choices can differ from
VHS's OpenCV loader.

| Format | Dimension multiple | Frame count |
|---|---|---|
| None | 1 | Any |
| AnimateDiff | 8 | Any |
| Mochi | 16 | 6n+1 |
| LTXV | 32 | 8n+1 |
| Hunyuan | 16 | 4n+1 |
| Cosmos | 16 | 8n+1 |
| Wan | 8 | 4n+1 |
| H3 | 32 | 17n+5 |

Presets trim trailing frames to the largest valid count without exceeding the
cap. Too few frames or a skip beyond the video ends with an explanatory error.
Preset constraints match the installed VHS presets; choose `None` for other
model variants or to preserve the exact selected batch.

Outputs: `images` is one RGB float32 IMAGE batch `[frames, height, width, 3]` in
0–1; `frame_count` is its actual length; `frame_rate` is the requested (or source)
FPS divided by every nth. Native variable-rate videos retain all selected source
frames and report an average FPS; set a positive FPS for uniform timing.
The `audio` output uses ComfyUI's standard AUDIO format and preserves source
sample rate and channels. It starts at the selected video interval and spans
`frame_count / frame_rate` seconds after preset trimming. Audio remains at its
original speed; every-nth selection reduces the output FPS accordingly. Missing
audio at the interval's edges is padded with silence. A file without an audio
track, or a disabled `load_audio`, returns `None`. Connect `audio` to a video
combine/save node's audio input or to Preview Audio when a track is available.

Long or high-resolution batches require RAM for the
full output (about 24 MiB per 1080p frame); use a cap or smaller dimensions.

---

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


## Qwen Edit Align

**Category:** `WepeNerd/Qwen Edit Align`. These operate on decoded images and
work with other image editors too. No model patch or RoPE correction is included.

### Qwen Edit Measure Drift

Connect one `source` and one `edited` image. `translation` estimates horizontal
and vertical drift; `affine` also fits scale, rotation and shear. Positive
`drift_x_px` means the edited content moved right, positive `drift_y_px` means
down. Values are in source-image pixels; for affine fits they describe movement
at the image centre. Connect `warp_params` to Align Composite to reuse the fit.
Optional `edit_mask` excludes intended changes from registration; white is excluded.
`ignore_border_px` excludes border artifacts and is reduced for very small images.
This node reports a single pair; select individual images before measuring a batch.

### Qwen Edit Align Composite

Aligns `edited` to `source`, then composites with `edit_mask` (white selects the
edit), or a simple threshold mask when no mask is connected. `grow_px` expands
the mask and `feather_px` softens its boundary. Outputs `composite`, `aligned_edit`,
`mask`, and reusable `warp_params`. The supplied mask is in source coordinates
and is also excluded from the alignment fit. Parameters measured at a different
resolution scale with each axis; use them only for the same image geometry.

### Qwen Edit Difference Mask + Composite

This is the dedicated auto-masker for additions, removals and replacements.
Connect the original image to `source` and the edited result to `edited`.
It optionally corrects translation drift, compares the images, cleans up the mask,
and pastes the selected edited pixels onto the original. Removed objects are
replaced by the background from the edited image.

| Control | Effect |
|---|---|
| `threshold` | Lower detects smaller differences; higher preserves more source pixels. Start at 0.08. |
| `align_first` | Correct translation before comparison. Enable for shifted edits; disable for already aligned images. |
| `diff_mode` | Max RGB detects any colour-channel change; luminance compares brightness; chroma compares colour relative to brightness. |
| `pre_blur_px` | Blur only the comparison images to suppress fine noise; compositing still uses the unblurred edit. |
| `min_region_px` | Remove connected regions below this area. Set 0 to retain tiny edits. |
| `fill_holes` | Fill enclosed gaps in the detected regions. Turn off to preserve holes or unchanged interiors. |
| `grow_px` | Expand the mask, or shrink it with a negative value. Set 0 for the threshold boundary. |
| `feather_px` | Soften the mask boundary. Set 0 for a hard selection. |
| `limit_mask` | Optional allowed region in source coordinates; black always preserves source, including after feathering. Also excluded from the alignment fit. |

Outputs: `composite`, `mask`, `preview` (red mask overlay on the source), and
`difference` (grayscale visual difference scaled around the threshold).
For affine drift, run Align Composite in `affine` mode first, then compare its
`aligned_edit` with the original using `align_first=false`.

Both compositors handle matching batches and broadcast a single source, edit or
mask over a batch. Each pair is aligned separately. Other batch-size mismatches
raise an error rather than dropping images. Edited images and masks are resized
to the source dimensions. Outputs are CPU float32; RGB and RGBA are supported.
If the edit lacks alpha, source alpha is retained before alignment; if the source
is RGB, edited alpha is discarded. Difference masking also detects alpha changes
when both images are RGBA.

Pixels where the **final mask is exactly zero** equal the source exactly.
Feathered pixels blend both images. Growth, blur and hole filling deliberately
include nearby or enclosed pixels; set them to zero/off for a tighter mask.
Warped coordinates outside the edited image retain the source. `aligned_edit`
itself uses replicated border pixels; the compositor's mask excludes these areas.

Registration needs enough unchanged, textured background and assumes the same
framing. Fits use a pyramid up to a 1024-pixel longest edge, while output warping
and masks use full source resolution. Flat or fully excluded backgrounds use
zero drift with a console warning. Large redraws, repeated patterns and local
deformations can confuse alignment. Difference masking is not semantic object
segmentation: strong unwanted changes can also be selected, while subtle parts
of an object can be missed. Inspect the mask and use `limit_mask` or Paint Mask
when you need a precise allowed edit region. No model download is required.

Validated with synthetic translations, affine transforms, additions/removals,
batches, mask limits and exact preservation checks. Real Qwen output quality
still depends on the source/edit pair and the chosen threshold.

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
and paint over it. Connect an upstream `IMAGE` to the optional `image` input and
press **Load** to run that image's upstream path and import the first image of its
batch. Each click queues the current upstream settings, without running downstream
nodes. With no image connected, **Load** opens the file picker. Normal **Queue**
outputs the saved painting; press **Load** again to replace it from upstream.
Width and height accept INT connections and local values
(64â€“4096 pixels, step 1). Imports use an oriented, colour-managed, centered Lanczos
crop at that exact size. Shift-drag before painting to reposition the crop.

The compact toolbar has round/square brushes, an eraser, colour with a hex picker, a live
pixel-size slider, stroke opacity, pen pressure to size, and undo. The size slider
gives small brushes more travel; its arrow keys and `[` / `]` adjust by one pixel.
Toggle the eraser button or press **E** to erase paint back to the loaded image
or original blank background; press **B** to paint again. Erasing uses the same
shape, size, opacity and pressure controls, and supports undo/redo.
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


## Paint Mask

**Category:** `WepeNerd/Image` · **Outputs:** `MASK`, `IMAGE`.

Open or drop an image, then paint with the same brush, rectangle, eraser, Size,
Softness, Opacity, Undo and Clear controls as Load LoRA Masked. The editor opens
expanded. Connect MASK downstream and Queue: full coverage is 1 (white), untouched
pixels are 0 (black), and soft/partial coverage is gray. The magenta overlay is only
a preview; the **Grayscale mask** toggle displays the saved coverage directly.
For an opened file, output uses its exact oriented dimensions; Clear keeps those
dimensions. IMAGE returns the original RGB image without the mask overlay.
Without an image, the canvas and IMAGE output are black at 1024 × 1024.

For a connected **image**, **Load input** queues its upstream path to load the
first batch image, even when a preview is already available. Upstream seed controls
follow their normal randomize/increment/fixed settings; ComfyUI may reuse unchanged
cached results. Reroutes and bypassed nodes are
resolved through ComfyUI's execution graph. Load input never runs downstream nodes.
Normal Queue runs refresh the editor preview and pass the entire connected IMAGE
batch through unchanged. The same painted mask is resized proportionally to the
connected image dimensions and repeated across the batch. The node can run as a
preview without a downstream output node attached.

Different-size image replacements ask before clearing a painting; same-size
replacements keep it. Undo also restores Clear and image replacements. Use the
node's **Open reference image…** menu to open a file while IMAGE is connected.
Removing the reference leaves the mask and its dimensions intact.

Saved workflows contain the mask and editor settings, with small file references
for source images instead of embedded base64 copies. Existing embedded references
migrate when opened, after the asset is verified or restored. Copy
`ComfyUI/input/wepenerd_paint_mask` and `ComfyUI/input/wepenerd_masked_lora` with
workflows when moving machines. Undo history is limited to 20 actions / 64 MiB
and lasts for the current editing session.

## Load LoRA Masked


**Category:** `WepeNerd/Loaders` Â· **Model:** native floating-point or INT8 ConvRot Krea2.

Use ComfyUI's **Load Diffusion Model** loader, including for native INT8 ConvRot
checkpoints. The base layer retains its quantized forward calculation; the masked
LoRA/LoKr contribution runs separately in the activation dtype. The masked node does
not expand the base weights to floating point. Ordinary global LoRA patches still
follow ComfyUI's own weight patching and requantization behavior.

Connect MODEL, choose an installed LoRA and set strength (negative values are supported).
Open **Edit mask** and paint with the brush, rectangle or eraser. Empty masks have no
effect. The magenta overlay is a fixed 45% display preview, separate from coverage.
Undo restores a whole gesture, image replacement or Clear.

**Size** is the diameter in source-image pixels. **Softness** changes the brush and
eraser edge falloff: 0% is hard; 100% has a broad smooth edge. **Opacity** controls
coverage per gesture. On an empty mask, one 50% stroke gives about 0.5 and a second
separate stroke gives about 0.75. Pausing or retracing within one gesture does not
keep building coverage. Erasing removes the same fraction of the starting mask.
Rectangles use opacity with hard edges; their Size and Softness controls are disabled.
Legacy workflows default to hard edges and 100% opacity.

The **Grayscale mask** toggle shows black-to-white coverage instead of the reference
overlay. Soft/gray coverage scales the selected LoRA strength. When MASK is connected,
**Using MASK input** and an editor notice identify the saved painting as inactive;
painting and grayscale preview still refer to that saved painting.

Connect the optional **mask** input to use a mask from another node instead of the
saved painting. Disconnect it to use the painting again. External masks accept
`[H, W]` or `[B, H, W]`, are copied to CPU float32, and retain their full resolution.
NaN/Infinity is rejected; finite values outside `[0, 1]` are clamped with a warning.
Negative LoRA strength is independent of mask coverage. Mask batches repeat or
truncate within each logical image batch, then repeat for each actual conditioning
chunk in the sampler's order. For masks A/B and image batch 3, two chunks use
`A B A | A B A`. Extra mask rows produce one warning per mask/batch layout during
each sampling run instead of being silently dropped.

The **MASK** output below **MODEL** returns the selected mask at its original
resolution for other mask nodes. White applies the LoRA, black has no effect;
gray scales the contribution continuously. The mask is available even when LoRA
strength is zero. Clear retains the editor dimensions using a versioned empty
marker. Legacy empty strings retain the historical 1024 × 1024 default. Masks are
limited to 16384 pixels per side, 64 Mi pixels total (including batch), and 96 MiB
of serialized text. Saved PNG dimensions must match their header; alpha stores
coverage and never the magenta preview.

Downsampling now uses area/adaptive averaging so narrow painted regions survive
projection to image tokens. Integer reductions are exact block averages; noninteger
reductions are adaptive averages. Upsampling is bilinear; mixed-axis resizing
reduces shrinking axes first. Geometry follows the actual latent size and model
patch size. For patch padding, coverage first maps to the unpadded latent, wraps
the right/bottom border circularly like native Krea2, then averages into tokens.
This intentionally fixes the previous bilinear aliasing at mask edges.

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

Mask PNGs (or empty markers), geometry, Size/Softness/Opacity, grayscale preview and
folded state are saved in the workflow. Saving, exporting or queuing captures the
current brush pixels without ending the drag. Pointer release commits one undo
step; Escape, cancellation or lost capture restores its starting mask, even after
a save during the gesture. Reference images use ComfyUI input-asset descriptors; copy the
`input/wepenerd_masked_lora` folder with workflows when moving machines. LoRA/model
files must also be installed on the destination machine. Duplicates have independent editors.
Hover updates only the cursor layer. Painting updates the preview once per animation
frame. Changed gestures encode PNGs asynchronously; an immediate save/queue falls
back to synchronous encoding when necessary. Coverage tracking avoids a full-mask
alpha scan after each stroke, and pointerdown reuses the existing document without
parsing its PNG text. See [editor and runtime review fixes](masked-lora-review-fixes.md)
for tested behavior, timings and remaining large-brush limits.
Failed undo restores report an error and retain both the current painting and
the history entry; successful restores remove the entry.

Chained masked nodes add independent spatial contributions and preserve ordinary
global LoRA weight patches. Supported adapters are linear LoRA and full/factored LoKr,
including alpha/rank scaling. LoHa, DoRA, convolution/Tucker, reshape, other adapter
formats and recognized unmapped adapter tensors are rejected before any patches
are applied, including unsupported tensors in omitted layers. Supported text,
timestep, modulation and normalization adapters are omitted and diagnosed separately.
A file without compatible spatial layers is an error. A black mask or zero strength
can bypass file inspection and does not certify the adapter's format.
Model-layer validation runs once on each patched projection; untouched layers stay
under the native model's handling. This does not certify other model formats.
Native still-image reference layouts `index` and
`index_timestep_zero` are supported; reference tokens themselves are not adapted.

A full mask need not equal a global loader because of the omitted layers. Attention and
denoising can spread indirect effects beyond the painted area; use final compositing
when exact pixel preservation is needed. Custom quantized loaders, GGUF, FP8, NVFP4,
video, cropped regional conditioning, tiled/context sampling, multi-GPU sampling,
and input-token rearrangement are unsupported. Unverified calls without conditioning
chunk metadata fail clearly. Future sample reordering/subsetting integrations must
supply explicit sample indices and global crop origins; local tensor dimensions are
insufficient.

**start_percent / end_percent** default to **0 / 1**. Range follows the model's
denoising progression; a partial-denoise run may use only part of it. Endpoints
use the active model sampling object's `percent_to_sigma`, including its shift,
and are inclusive. Equal endpoints disable the adapter; reversed or nonfinite
ranges are errors. Full 0–1 always applies, including custom sigma excursions.
Repeated evaluations at the same sigma make the same decision; revisiting an
allowed sigma in a nonmonotonic schedule activates the adapter again.

**apply_to** defaults to **Both**. **Positive only** gates direct adapter injection
to positive conditioning chunks using native KSampler/CFGGuider/BasicGuider
metadata. It supports split CFG calls and CFG=1, but rejects custom guiders and
custom conditioning/prediction wrappers whose branch meaning is unverified.
Mixed batches compute adapter deltas only for the active rows.
This changes interaction with CFG; it does not always improve quality, change
text encoder weights, or schedule other nodes' global LoRAs. Chained masked nodes
can use independent ranges and modes. Old workflow/API fields default to full
range/Both. See [Phase 4 sampling validation](masked-lora-phase4.md).

Chained regions share one layer dispatcher and one sampling layout. Adapter tensors
and projected masks are reused within each sampling run, with a default 256 MiB
residency cap and 1 GiB free-memory headroom. Entries are converted lazily after
model preparation and released on completion, error or interruption. Entries that
fit stay resident; adapter overflow streams without evicting the working set on
every layer visit. New projected masks get admission priority, displacing adapters
first, so layout changes can reuse masks across layers. Byte/entry limits still
apply; a disabled cache or a mask larger than the cap streams. Actual device-memory
pressure can also evict cached entries. Set
`WEPENERD_MASKED_LORA_CACHE_MB=0` before starting ComfyUI for low-memory streaming;
this trades repeated transfers/projection for less retained memory. A large
adapter still needs working memory for its native calculation.
Invalid cache/headroom environment values warn and use their defaults.

Unchanged adapter files reuse a bounded CPU cache, including after mask-only edits.
Ordinary file replacement is detected when queuing. Restart ComfyUI to force a
fresh read if a writer preserved all file metadata. See
[Phase 2 runtime and measurements](masked-lora-phase2.md) for cache settings,
in-process invalidation and measured RTX 5090 tradeoffs.

The compatibility boundary remains native floating-point and native INT8 ConvRot
Krea2. See [Phase 1 validation](masked-lora-phase1.md) for actual numerical and
integration results, the Phase 2 report for runtime regressions, and Phase 4 for
fixed-seed generation evidence and its visual-quality limits. FP8/NVFP4, Qwen and
Flux remain unsupported.

## Sigma Curve

Draw a noise schedule and output `SIGMAS` for **SamplerCustom**, **SamplerCustomAdvanced**,
or any node with a `sigmas` input. Find it under **WepeNerd/Sampling**.

The line is the curve; the small dots are the exact sigmas the node outputs, one per
step. Large dots are control points.

| Action | How |
|---|---|
| Add a point | Click anywhere in the graph (keep holding to drag it) |
| Move a point | Drag it. With **Snap** on, points land on step positions; hold Shift to invert snapping |
| Delete a point | Right-click, double-click, Alt-click, drag it off the graph, or select it and press Del |
| Exact values | Select a point and type its `step` or `σ` in the fields under the graph |
| Nudge | Tab / Shift+Tab selects points; arrows move them (Shift for bigger steps). Esc deselects |
| Undo / Redo | ↶ ↷ buttons, Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z |
| Inspect | Hover to read each step's sigma and the next one |

The first and last points are pinned to the start and end of the schedule; you can
change their height but not remove them.

| Input | Description |
|---|---|
| `steps` | Sampling steps. The curve is sampled at evenly spaced step positions, so changing steps keeps the shape |
| `sigma_max` | Sigma at the top of the graph. Points are stored relative to it, so changing it rescales the curve. Use about 14.61 for SD1.5/SDXL and 1.0 for flow models (Flux, SD3, Wan) |
| `sigma_min` | Lowest sigma used by presets and the bottom of the **Log** view. It does not clamp the output |
| `interpolation` | `log smooth` (default) bends in log-sigma space, so exponential/Karras-like curves need few points. `smooth` is a monotone cubic in linear space. `linear` joins points with straight lines. None of them overshoot between points |
| `end_at_zero` | On: output is `steps` curve values plus a final 0 (ComfyUI's usual layout; shown as a dashed drop at the end). Off: the curve supplies all `steps + 1` values |

**Preset** replaces the curve with a fitted Karras, exponential, poly-exponential,
linear, Align Your Steps (SDXL), flow shift 3/6, or cosine schedule over
`sigma_min`–`sigma_max`, and sets a matching interpolation. **Log** changes only the
view and helps with low-sigma detail. **Copy** puts the output sigmas on the clipboard.
**Paste** reads a list such as `14.6, 6.3, 3.7, …, 0` and creates one point per value,
setting `steps`, `sigma_max`, and `sigma_min` to match.

A warning appears in the corner if the schedule rises anywhere; most samplers expect
sigmas to decrease.
