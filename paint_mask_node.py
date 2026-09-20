"""Standalone mask painting using the shared masked LoRA editor."""

import base64
import io
import json
from pathlib import Path

import numpy as np
import folder_paths
from PIL import Image
import torch
import torch.nn.functional as F

from .masked_lora_node import decode_mask, save_asset


def source_image(source):
    if isinstance(source, str):
        # Read existing workflows until the editor migrates their embedded source.
        if not source.startswith("data:image/png;base64,"):
            raise ValueError("Paint Mask: reload the source image.")
        path = io.BytesIO(base64.b64decode(source.split(",", 1)[1], validate=True))
    else:
        if not isinstance(source, dict) or source.get("type") != "input":
            raise ValueError("Paint Mask: invalid source image asset.")
        root = Path(folder_paths.get_input_directory()).resolve()
        path = (root / source.get("subfolder", "") / (source.get("name") or source.get("filename", ""))).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Paint Mask: source image is outside the input folder.")
        if not path.is_file():
            raise ValueError("Paint Mask: source image is missing. Restore its input asset folder or reopen the image.")
    with Image.open(path) as opened:
        return torch.from_numpy(np.array(opened.convert("RGB"), dtype=np.float32) / 255)[None]


class WN_PaintMask:
    DESCRIPTION = "Open/drop an image, or connect IMAGE and click Load input. Paint white mask regions, then Queue. IMAGE passes the connected batch through unchanged, or returns the opened image. The mask fits the output image dimensions."
    CATEGORY = "WepeNerd/Image"
    RETURN_TYPES = ("MASK", "IMAGE")
    FUNCTION = "paint"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "mask_data": ("STRING", {"default": "", "multiline": False}),
        }, "optional": {
            "image": ("IMAGE", {"tooltip": "Passed through unchanged. Load input previews the first image for painting; Queue also refreshes the preview."}),
        }}

    def paint(self, mask_data="", image=None):
        try:
            mask, _ = decode_mask(mask_data)
        except ValueError as error:
            raise ValueError("Paint Mask: could not read the saved mask. Reopen the editor or reload the image.") from error
        connected = image is not None
        if not connected:
            source = json.loads(mask_data).get("source") if mask_data else None
            if source:
                image = source_image(source)
            else:
                image = torch.zeros((1, *mask.shape[-2:], 3), dtype=torch.float32)
        if mask.shape[-2:] != image.shape[1:3]:
            mask = F.interpolate(mask, size=image.shape[1:3], mode="bilinear", align_corners=False)
        result = (mask.squeeze(1).expand(image.shape[0], -1, -1), image)
        if connected:
            preview = Image.fromarray((image[0].detach().cpu().clamp(0, 1).numpy() * 255).astype(np.uint8))
            return {"ui": {"paint_mask_source": [save_asset(preview)]}, "result": result}
        return result
