# ComfyUI-WepeNerd

Resolution tools, a compact sketch canvas, and spatial LoRA masks for ComfyUI.

| Tool | What it does |
|---|---|
| **Drag Resolution** | Set dimensions visually with aspect-ratio and divisor controls |
| **Resolution Suggest** | Calculate dimensions from a target size or scale factor |
| **Resize Image Megapixels** | Resize an image batch to a target area without cropping |
| **Speedpaint** | Sketch from a blank canvas or imported image and output an IMAGE |
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

Restart ComfyUI and refresh the browser. This pack uses Pillow and numpy, plus
Torch supplied by ComfyUI. It does not install GPU wheels, models, or external runtimes.

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
- [Experimental](https://github.com/WepeNerd/ComfyUI-WepeNerd-Experimental): 3D, Liquify, and video utilities.

The packages can be installed independently. Use current versions when combining
them; the older all-in-one core includes duplicate nodes.

[Report an issue](https://github.com/WepeNerd/ComfyUI-WepeNerd/issues) with a minimal
workflow and the relevant console output. Licensed under [MIT](LICENSE).
