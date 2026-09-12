"""Resize images to a megapixel target with divisible dimensions."""

import math

import comfy.utils


DIVISORS = (2, 4, 8, 16, 32, 64)


def megapixel_dimensions(width, height, megapixels, divisor):
    """Snap the ideal aspect-preserving dimensions to the nearest multiples."""
    megapixels = float(megapixels)
    divisor = int(divisor)
    if not math.isfinite(megapixels) or megapixels <= 0:
        raise ValueError("Megapixels must be a finite number greater than zero.")
    if divisor not in DIVISORS:
        raise ValueError("Divisor must be 2, 4, 8, 16, 32 or 64.")
    scale = math.sqrt(megapixels * 1_000_000 / (width * height))
    return tuple(
        max(1, math.floor(dimension * scale / divisor + 0.5)) * divisor
        for dimension in (width, height)
    )


class WN_ResizeMegapixels:
    CATEGORY = "WepeNerd/Image"
    FUNCTION = "resize"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    DESCRIPTION = (
        "Resize to a megapixel target, keeping the input aspect ratio as closely "
        "as dimension rounding allows. Both dimensions are rounded to the nearest "
        "multiple of the divisor. No cropping. 1 MP = 1,000,000 pixels."
    )
    UPSCALE_METHODS = ["lanczos", "bicubic", "bilinear", "nearest-exact", "area"]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "megapixels": ("FLOAT", {
                    "default": 1.0, "min": 0.01, "max": 100.0, "step": 0.01,
                    "tooltip": "Target image area; 1 MP = 1,000,000 pixels. Rounding may go above or below it.",
                }),
                "divisor": ([str(value) for value in DIVISORS], {
                    "default": "8",
                    "tooltip": "Round width and height to the nearest multiple of this number.",
                }),
                "upscale_method": (cls.UPSCALE_METHODS,),
            }
        }

    def resize(self, image, megapixels, divisor, upscale_method):
        width, height = megapixel_dimensions(
            image.shape[2], image.shape[1], megapixels, divisor
        )
        if (width, height) == (image.shape[2], image.shape[1]):
            return (image,)
        resized = comfy.utils.common_upscale(
            image.movedim(-1, 1), width, height, upscale_method, "disabled"
        )
        return (resized.movedim(1, -1),)


NODE_CLASS_MAPPINGS = {"WN_ResizeMegapixels": WN_ResizeMegapixels}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_ResizeMegapixels": "Resize Image Megapixels (WepeNerd)"}
