"""Paint an edit region and turn it into a visual prompt for Qwen-Image 2.1 editing."""

import json
import math

import numpy as np
from PIL import Image
from scipy import ndimage
import torch
import torch.nn.functional as F

from .masked_lora_node import decode_mask, save_asset
from .paint_mask_node import source_image

GUIDES = ["outline", "tint", "solid", "none (use mask_image)"]
COLORS = {
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.25, 1.0),
    "yellow": (1.0, 0.9, 0.0),
    "magenta": (1.0, 0.0, 1.0),
    "cyan": (0.0, 1.0, 1.0),
    "white": (1.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0),
}
TINT_ALPHA = 0.5


def qwen_size(width, height, resolution):
    """Mirror TextEncodeQwenImage21's reference resize (multiples of 32, ~resolution² pixels)."""
    if resolution > 0:
        ratio = width / height
        w = round(math.sqrt(resolution * resolution * ratio) / 32) * 32
        h = round(math.sqrt(resolution * resolution / ratio) / 32) * 32
    else:
        w, h = round(width / 32) * 32, round(height / 32) * 32
    return max(32, w), max(32, h)


def resize_image(image, width, height):
    if tuple(image.shape[1:3]) == (height, width):
        return image
    import comfy.utils

    samples = image.movedim(-1, 1)
    return comfy.utils.common_upscale(samples, width, height, "lanczos", "disabled").movedim(1, -1).clamp(0, 1)


def resize_mask(mask, width, height):
    """mask: (1, 1, H, W) coverage."""
    if tuple(mask.shape[-2:]) == (height, width):
        return mask
    shrinking = height < mask.shape[-2] or width < mask.shape[-1]
    return F.interpolate(mask, size=(height, width), mode="bilinear", align_corners=False,
                         antialias=shrinking).clamp(0, 1)


def guide_alpha(coverage, guide, line_px):
    """Per-pixel opacity of the painted guide for a (H, W) coverage array."""
    if guide == "tint":
        return coverage * TINT_ALPHA
    if guide == "solid":
        return coverage.copy()
    if guide != "outline":
        return np.zeros_like(coverage)
    inside = coverage >= 0.5
    if not inside.any() or inside.all():
        return np.zeros_like(coverage)
    # Antialiased ring hugging the region from outside, so the region itself stays visible.
    distance = ndimage.distance_transform_edt(~inside)
    return np.clip(line_px + 0.5 - distance, 0, 1).astype(np.float32) * (distance > 0)


def region_prompt(guide, color, instruction):
    if guide == "outline":
        region, cleanup = f"the area inside the {color} outline", f"Remove the {color} outline."
    elif guide == "tint":
        region, cleanup = f"the area highlighted in translucent {color}", f"Remove the {color} highlight."
    elif guide == "solid":
        region, cleanup = f"the area painted solid {color}", f"No {color} paint remains."
    else:
        region, cleanup = "the area that is white in the mask <image2>", "Do not copy the mask image."
    instruction = " ".join((instruction or "").split()).rstrip(" .")
    # The Qwen-Image 2.1 text encoder labels reference slots <image1>, <image2>, ...
    lead = f"In <image1>, edit only {region}"
    lead = f"{lead}: {instruction}." if instruction else f"{lead}."
    return f"{lead} {cleanup} Keep everything outside that area unchanged."


class WN_QwenEditMask:
    DESCRIPTION = (
        "Paint where a Qwen-Image 2.1 edit should happen. Outputs the image with a visual guide "
        "(outline, tint or solid paint) resized to the Qwen reference grid, the clean image, the "
        "mask, a black/white mask image, and a ready prompt. Connect image to Text Encode Qwen "
        "Image 2.1 image_1 and set its resolution to 0 so the guide lands exactly where you painted."
    )
    CATEGORY = "WepeNerd/Qwen Edit Align"
    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK", "IMAGE", "STRING")
    RETURN_NAMES = ("image", "source", "mask", "mask_image", "prompt")
    OUTPUT_TOOLTIPS = (
        "Source with the guide drawn in; connect to image_1.",
        "Clean source at the same size; use for compositing the result back.",
        "Painted coverage at the output size (white = edit).",
        "Mask as a black/white IMAGE, for the 'none' guide as image_2.",
        "Your instruction wrapped with a description of the guide.",
    )
    FUNCTION = "build"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "mask_data": ("STRING", {"default": "", "multiline": False}),
            "guide": (GUIDES, {"default": "outline", "tooltip": "How the painted region is shown to the model. "
                               "outline draws a ring around it, tint overlays it, solid paints over it (the model "
                               "cannot see what was there), none leaves the image clean for use with mask_image."}),
            "color": (list(COLORS), {"default": "red", "tooltip": "Guide colour. Pick one that does not occur in the region."}),
            "fill_holes": ("BOOLEAN", {"default": True, "tooltip": "Treat areas enclosed by paint as part of the "
                                       "region, so a quick loop drawn around an object selects the whole object."}),
            "line_px": ("INT", {"default": 6, "min": 1, "max": 64, "tooltip": "Outline thickness in output pixels."}),
            "resolution": ("INT", {"default": 1024, "min": 0, "max": 4096, "step": 32,
                                   "tooltip": "Same rule as Text Encode Qwen Image 2.1: about resolution × resolution "
                                              "pixels in multiples of 32, keeping the aspect ratio. 0 keeps the image "
                                              "size rounded to 32. Set the encoder's resolution to 0 to avoid a second resize."}),
            "instruction": ("STRING", {"default": "", "multiline": True, "dynamicPrompts": True,
                                       "tooltip": "What should happen in the painted region, e.g. 'replace it with a red umbrella'."}),
        }, "optional": {
            "image": ("IMAGE", {"tooltip": "Image to edit. Load input previews the first image for painting."}),
        }}

    def build(self, mask_data="", guide="outline", color="red", fill_holes=True, line_px=6, resolution=1024,
              instruction="", image=None):
        if guide not in GUIDES:
            raise ValueError(f"Qwen Edit Mask: unknown guide '{guide}'.")
        if color not in COLORS:
            raise ValueError(f"Qwen Edit Mask: unknown colour '{color}'.")
        try:
            mask, _ = decode_mask(mask_data)
        except ValueError as error:
            raise ValueError("Qwen Edit Mask: could not read the saved mask. Reopen the editor or reload the image.") from error
        connected = image is not None
        if not connected:
            source = json.loads(mask_data).get("source") if mask_data else None
            if not source:
                raise ValueError("Qwen Edit Mask: open or drop an image in the editor, or connect IMAGE.")
            image = source_image(source)
        image = image.detach().cpu().float()
        mask = mask.detach().cpu().float()
        if mask.shape[-2:] != image.shape[1:3]:
            mask = F.interpolate(mask, size=image.shape[1:3], mode="bilinear", align_corners=False)

        width, height = qwen_size(image.shape[2], image.shape[1], resolution)
        clean = resize_image(image, width, height)
        mask = resize_mask(mask, width, height)

        coverage = mask[0, 0].numpy()
        if fill_holes:
            coverage = np.maximum(coverage, ndimage.binary_fill_holes(coverage >= 0.5).astype(np.float32))
            mask = torch.from_numpy(coverage)[None, None]
        alpha = torch.from_numpy(guide_alpha(coverage, guide, line_px))[None, :, :, None]
        colour = torch.tensor(COLORS[color])
        guided = clean.clone()
        guided[..., :3] = clean[..., :3] * (1 - alpha) + colour * alpha
        if guided.shape[-1] == 4:
            guided[..., 3:] = torch.maximum(clean[..., 3:], alpha)

        count = image.shape[0]
        mask_out = mask[:, 0].expand(count, -1, -1).contiguous()
        mask_image = mask_out[..., None].expand(-1, -1, -1, 3).contiguous()
        result = (guided, clean, mask_out, mask_image, region_prompt(guide, color, instruction))
        if connected:
            preview = Image.fromarray((image[0, ..., :3].clamp(0, 1).numpy() * 255).astype(np.uint8))
            return {"ui": {"paint_mask_source": [save_asset(preview)]}, "result": result}
        return result


NODE_CLASS_MAPPINGS = {"WN_QwenEditMask": WN_QwenEditMask}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_QwenEditMask": "Qwen Edit Mask (WepeNerd)"}
