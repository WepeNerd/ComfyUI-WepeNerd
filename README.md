# ComfyUI-WepeNerd

Resolution tools, painting and image warping, and spatial LoRA masks for ComfyUI.

| Tool | What it does |
|---|---|
| **Drag Resolution** | Set dimensions visually with aspect-ratio and divisor controls |
| **Resolution Suggest** | Calculate dimensions from a target size or scale factor |
| **Resize Image Megapixels** | Resize an image batch to a target area without cropping |
| **Load Video (Upload)** | Sample video into an IMAGE batch with frame controls, optional audio, VHS presets, and an autoplaying preview |
| **Speedpaint** | Sketch from a blank canvas or imported image and output an IMAGE |
| **Paint Mask** | Paint a MASK over an uploaded or connected image and pass the IMAGE downstream |
| **Liquify Image** | Push-warp files or connected IMAGE batches with editable strokes and full-resolution output |
| **Slider** | Map a compact FLOAT control to your preferred strength range |
| **Load LoRA Masked · Beta** | Apply spatial LoRA/LoKr regions to native Krea2 models |

## Installation

From your ComfyUI `custom_nodes` directory:

```sh
git clone https://github.com/WepeNerd/ComfyUI-WepeNerd.git
```

Using the Python environment that runs ComfyUI:

```sh
python -m pip install -r ComfyUI-WepeNerd/requirements.txt
```

Restart ComfyUI and refresh the browser. This pack uses Pillow, numpy, and PyAV,
plus Torch supplied by ComfyUI. It does not install GPU wheels, models, or external runtimes.

## Quick start

Open the [example workflow](examples/resolution-speedpaint.json). Resolution
Suggest controls Speedpaint's width and height; paint and click **Queue** to
preview the result. Connect Speedpaint to **VAE Encode** to use it in a generation
workflow.

- **Speedpaint:** choose **New** for a blank background, or **Load** / drop an image.
  Use round or square brushes, colour, size, opacity, and pressure-to-size controls.
  Ctrl/Cmd+Z undoes; Shift+Ctrl/Cmd+Z redoes; Alt-click samples a colour.
- **Resolution tools:** choose a divisor that matches your model's required
  dimensions. Megapixel resizing retains the whole image, with small aspect changes
  possible from rounding.
- **Load Video (Upload):** upload/select a video under **WepeNerd/Video** and
  connect `images` to an IMAGE input. Set frame rate, skip, stride, and cap as
  needed. Zero frame rate preserves source frames; zero cap loads all selected
  frames. Format presets can trim the batch; `None` preserves its selected count.
  The preview autoplays muted. Connect `audio` downstream to keep the selected
  clip's soundtrack, or disable `load_audio` to skip audio decoding.
- **Liquify:** load an image, or connect IMAGE and Queue once to see its preview.
  Drag to warp, use Undo/Redo, then Queue to render at the source resolution.
  Try the [Liquify example](examples/liquify.json).
- **Masked LoRA:** connect a native Krea2 MODEL, select a LoRA, and open **Edit mask**.
  Empty masks have no effect. This node is specific to Krea2 and remains in Beta.

See the [node guide](docs/nodes.md) for controls, inputs, outputs, and limitations.

## Saving paintings

Speedpaint supports dimensions from 64 to 4096 pixels and PNG, JPEG, or WebP imports.
It has file import rather than an upstream IMAGE socket. Resizing painted work
resizes the whole composition with Lanczos.

Paintings are stored in `ComfyUI/input/wepenerd_speedpaint`. Copy that folder with
your workflows when moving machines. Standard Save/Export and Queue wait for
pending writes. Undo and redo share a 30-operation / 128 MiB history budget;
history does not persist after reloading.

## Compatibility

Tested with ComfyUI 0.34.0, frontend 1.51.10, Python 3.12, and Windows. Browser and
CPU/CUDA layer checks are included; physical tablet behavior and full-model Krea2
image fidelity have not been comprehensively validated.

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
