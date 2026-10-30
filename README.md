# ComfyUI-WepeNerd

Resolution tools, video loading and saving, painting and image warping, spatial LoRA masks and
H3 Ripple video edit propagation for ComfyUI.

| Tool | What it does |
|---|---|
| **Drag Resolution** | Set dimensions visually with aspect-ratio and divisor controls |
| **Resolution Suggest** | Calculate dimensions from a target size or scale factor |
| **Resize Image Megapixels** | Resize an image batch to a target area without cropping |
| **VAE DeGrid** | Remove the 2px pixel grid that Qwen Image, Qwen Image 2.1 and Wan 2.1 VAEs leave in decoded images and video, without softening real detail |
| **Zoom Crop** | Remove width/height pixels and pick the kept area with a one-click 5×5 picker |
| **Load Video (Upload)** | Load a clip by frame cap and skip into an IMAGE batch, with audio, transparency mask, model frame presets, a readout of what will load, and a preview that loops the clip |
| **Save Video** | Save an IMAGE batch as MP4 (H.264, H.265, AV1), WebM, ProRes, lossless FFV1, GIF or WebP in one pass, with audio, transparency and the workflow embedded; no FFmpeg executable needed |
| **Duration Selector** | Pick a clip length that is legal for LTX, MiniMax H3, Wan, Hunyuan, Cosmos or Mochi, as frames or seconds kept in sync |
| **Frame Slicer** | Keep a run of frames (`start` to `stop`) from an IMAGE batch, like RES4LYF's Frames Slice |
| **Speedpaint** | Sketch from a blank canvas or imported image and output an IMAGE |
| **Paint Mask** | Paint a MASK over an uploaded or connected image and pass the IMAGE downstream |
| **Liquify Image** | Push-warp files or connected IMAGE batches with editable strokes and full-resolution output |
| **Qwen Edit Mask** | Paint where a Qwen-Image 2.1 edit should happen and get a guided reference image, mask and prompt |
| **Image Edit Align Composite** | Automatically align Qwen, Krea2 or other image edits and composite through a supplied or automatic mask |
| **Image Edit Difference Mask** | Align and isolate visual edits, with mask preview and cleanup controls |
| **Image Edit Measure Drift** | Optional measurement for inspecting or reusing an alignment fit |
| **Sigma Curve** | Draw a SIGMAS schedule by dragging points on a graph, with presets and exact numeric entry |
| **Slider** | Map a compact FLOAT control to your preferred strength range |
| **Load LoRA Masked · Beta** | Apply spatial LoRA/LoKr regions to native Krea2 models |
| **H3 Ripple** | Edit a frame of a shot in any image editor; MiniMax H3 carries the edit through the whole shot |

## Installation

From your ComfyUI `custom_nodes` directory:

```sh
git clone https://github.com/WepeNerd/ComfyUI-WepeNerd.git
```

Using the Python environment that runs ComfyUI:

```sh
python -m pip install -r ComfyUI-WepeNerd/requirements.txt
```

Restart ComfyUI and refresh the browser. This pack uses Pillow, numpy, SciPy, and PyAV,
plus Torch supplied by ComfyUI. It does not install GPU wheels, models, or external runtimes.

## Quick start

Add **Speedpaint** and connect its IMAGE output to **Preview Image**. Paint and
click **Queue** to preview the result. Connect **Resolution Suggest** to its width
and height inputs to control the canvas size, or **VAE Encode** to use the painting
in a generation workflow.

- **Speedpaint:** choose **New** for a blank background. **Load** runs a connected
  `image` upstream and imports its first image; without a connection it opens the
  file picker. You can also drop an image onto the canvas.
  Use round or square brushes, colour, size, opacity, and pressure-to-size controls.
  Toggle the eraser (**E**) to restore the loaded image or blank background; **B** returns to painting.
  Ctrl/Cmd+Z undoes; Shift+Ctrl/Cmd+Z redoes; Alt-click samples a colour.
- **Resolution tools:** choose a divisor that matches your model's required
  dimensions. Megapixel resizing retains the whole image, with small aspect changes
  possible from rounding.
- **Load Video (Upload):** upload/select a video under **WepeNerd/Video** and
  connect `images` to an IMAGE input. Pick the clip with `skip_first_frames` and
  `frame_load_cap` (121 by default; 0 loads all frames); large skips jump straight
  there. The node shows what will load before you run it, and the muted preview
  loops over exactly that clip. A format preset trims the frame count to a model's
  rule and sets its frame rate. Connect `audio` downstream to keep the clip's
  soundtrack, or disable `load_audio` to skip audio decoding.
- **Save Video:** connect `images` (and `audio`/`mask` if you have them) to
  **WepeNerd/Video › Save Video**. MP4 · H.264 has broad player support; pick WebM, ProRes,
  FFV1, GIF or WebP when you need transparency. Drop the saved file onto the canvas
  to reload its workflow.
- **VAE DeGrid:** put it straight after **VAE Decode** (before any resize, sharpener or
  upscaler) and leave the defaults. It shows the grid strength it measured; a batch with
  no grid passes through untouched. See [VAE DeGrid](docs/nodes.md#vae-degrid).
- **Liquify:** load an image, or connect IMAGE and Queue once to see its preview.
  Drag to warp, use Undo/Redo, then Queue to render at the source resolution.
- **Masked LoRA:** connect a native Krea2 MODEL, select a LoRA, and open **Edit mask**.
  Empty masks have no effect. This node is specific to Krea2 and remains in Beta.
- **Qwen Edit Mask:** open or drop an image (or connect IMAGE and click **Load input**),
  paint the area to change, and type the edit in `instruction`. Connect `image` to
  `image_1` and `prompt` to `prompt` on **Text Encode Qwen Image 2.1**, and set the
  encoder's `resolution` to 0. See the [guide options](docs/nodes.md#qwen-edit-mask).
- **Image Edit alignment:** under **WepeNerd/Image Edit**, add **Image Edit Align
  Composite**, connect the original to `source` and the generated edit to `edited`,
  then save `composite`. Affine alignment and a cleaned automatic mask are enabled
  by default; Measure Drift is optional. Use **Image Edit Difference Mask** for a
  mask preview and finer cleanup controls. Pixels where the final mask is zero
  are copied exactly from the source. See the [controls and limits](docs/nodes.md#image-edit-alignment).

- **H3 Ripple:** open the [frame-upload workflow](example_workflows/H3_Ripple.json), or the
  [local image-editor workflow](example_workflows/H3_Ripple_Local_Editor.json) with two complete
  Qwen Image 2.1 editing branches. Both use **Ripple Clip → Ripple Apply → Ripple Output**
  and require MiniMax H3 FL2VA, its text encoder and video VAE, and `H3_Ripple_v1`.
  Pick a video, time window, duration and megapixels; preview the crop before editing. The starting
  size is **0.25 MP**, with **107 frames / 4.46 seconds**. Download and edit a marked frame, then
  upload, drop or paste it onto its card. Add a second anchor when another view needs the same change.
  Changing only megapixels resizes the existing edits; changing the crop or frame needs a fresh review.
  The local-editor workflow starts with Ripple Output muted: run and review both Qwen images first,
  then enable Ripple Output and run the video. Source audio and a source | result comparison are saved.
  See [H3 Ripple](docs/nodes.md#h3-ripple) for model files, geometry and anchor routing.

See the [node guide](docs/nodes.md) for controls, inputs, outputs, and limitations.

## Saving paintings

Speedpaint supports dimensions from 64 to 4096 pixels and PNG, JPEG, or WebP imports.
Use **Load** to import a file or the first image from the optional IMAGE input.
Resizing painted work resizes the whole composition with Lanczos.

Paintings are stored in `ComfyUI/input/wepenerd_speedpaint`. Copy that folder with
your workflows when moving machines. Standard Save/Export and Queue wait for
pending writes. Undo and redo share a 30-operation / 128 MiB history budget;
history does not persist after reloading.

## Compatibility

Use a current ComfyUI version with the native model nodes required by your workflow.
Python 3.10 or newer is required.

Masked LoRA supports native floating-point and INT8 ConvRot Krea2. Spatial masks
can influence pixels outside the painted region through attention and denoising.
A full mask does not necessarily match a global LoRA loader. See the node guide
for unsupported model and adapter formats.

## More WepeNerd tools

- [LocalAI](https://github.com/WepeNerd/ComfyUI-WepeNerd-LocalAI): local prompt enhancement and captioning.
- [Experimental](https://github.com/WepeNerd/ComfyUI-WepeNerd-Experimental): 3D and video utilities.

The packages can be installed independently. Use current versions when combining
them; the older all-in-one core includes duplicate nodes.

Liquify is included in core from **0.2.0**. If you also have Experimental installed,
update it to **0.2.0 or newer** before restarting ComfyUI. Existing Liquify nodes
and saved paintings keep their IDs and outputs.

[Report an issue](https://github.com/WepeNerd/ComfyUI-WepeNerd/issues) with a minimal
workflow and the relevant console output. Licensed under [MIT](LICENSE).
