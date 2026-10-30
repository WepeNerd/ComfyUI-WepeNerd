"""VAE DeGrid: remove the 2px pixel grid left by Qwen Image / Wan 2.1 style VAEs."""

try:
    from . import vae_degrid_core as core
except ImportError:  # loaded standalone by the tests
    import vae_degrid_core as core


def _host():
    """Compute device, interrupt check and progress bar from ComfyUI, if running in it."""
    try:
        import comfy.model_management as mm
        import comfy.utils
    except ImportError:
        return None, None, None
    return mm.get_torch_device(), mm.throw_exception_if_processing_interrupted, comfy.utils.ProgressBar


class WN_VAEDeGrid:
    """Measure and subtract the 2px VAE lattice, leaving real fine detail alone."""

    CATEGORY = "WepeNerd/Image"
    FUNCTION = "degrid"
    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("image", "removed_grid")
    OUTPUT_TOOLTIPS = (
        "The image with the grid removed: same size, channels and frame count as the input.",
        "Preview of what was removed (see the preview setting). For inspection only.",
    )
    DESCRIPTION = (
        "Removes the faint 2px pixel grid that the Qwen Image, Qwen Image 2.1 and Wan 2.1 "
        "VAEs (Krea2, Anima and others built on them) leave in decoded images and video "
        "frames. Connect it straight after VAE Decode, before any resize, sharpening, "
        "upscaler or restorer: those amplify the grid or scramble it beyond repair.\n\n"
        "The node measures the lattice at every pixel and subtracts only that, so fine "
        "texture and sharp edges pass through instead of being filtered. A batch with no "
        "grid (an upscaled or resized image, say) is passed through untouched. After a "
        "run the node shows the grid strength it measured."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "Connect straight from VAE Decode (images or video frames)."}),
                "strength": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "How much of the measured grid to subtract. 1 removes it; 0 leaves "
                               "the image untouched (handy for A/B checks).",
                }),
                "window": ("FLOAT", {
                    "default": core.DEFAULT_WINDOW, "min": core.MIN_WINDOW, "max": core.MAX_WINDOW,
                    "step": 0.5,
                    "tooltip": "Size in pixels of the area over which the grid's strength is "
                               "measured. Smaller follows a grid whose strength changes quickly "
                               "across the image; larger is more precise and spares fine texture "
                               "even better. The default suits most images.",
                }),
                "skip_when_clean": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Pass the batch through bit-for-bit when no grid is found (under "
                               "0.5/255). The decision covers the whole batch, so video frames are "
                               "all treated alike. Turn off to remove whatever is found anyway.",
                }),
                "preview": (list(core.PREVIEWS), {
                    "default": core.PREVIEWS[0],
                    "tooltip": "What removed_grid shows. strength map: where the grid was strong "
                               "(brighter = stronger, readable at any size). lattice zoom: a "
                               "magnified centre crop of the removed pattern, where the 2px grid "
                               "is visible. lattice full frame: the whole removed pattern (looks "
                               "like gray noise unless viewed at 100%).",
                }),
            }
        }

    def degrid(self, image, strength=1.0, window=core.DEFAULT_WINDOW, skip_when_clean=True,
               preview=core.PREVIEWS[0]):
        device, interrupted, progress_bar = _host()
        bar = progress_bar(image.shape[0]) if progress_bar and image.shape[0] > 1 else None

        def on_frame(_index):
            if interrupted:
                interrupted()
            if bar:
                bar.update(1)

        cleaned, removed, amplitude, stats = core.degrid(
            image, strength=strength, window=window, skip_when_clean=skip_when_clean,
            device=device, on_frame=on_frame)
        shown = core.preview(removed, amplitude, preview).to(image.device)
        return {"ui": {"text": core.status_lines(stats)}, "result": (cleaned, shown)}


NODE_CLASS_MAPPINGS = {"WN_VAEDeGrid": WN_VAEDeGrid}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_VAEDeGrid": "VAE DeGrid (WepeNerd)"}
