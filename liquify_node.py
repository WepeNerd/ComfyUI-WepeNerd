import base64
import hashlib
import io

import numpy as np
import torch
from PIL import Image


class WN_LiquifyImage:
    """
    Loads an image inside the node, lets the user liquify/push-warp it with a
    browser brush UI, and outputs the latest warped result.

    Frontend editing happens in js/wn_liquify.js. The frontend stores the
    current warped result as a base64 PNG in the hidden `image_data` widget.
    At execution time this backend decodes that string into a standard ComfyUI
    IMAGE tensor and a MASK from the alpha channel.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_data": ("STRING", {"default": "", "multiline": False}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "process"
    CATEGORY = "WepeNerd/Image"

    def _empty(self):
        image = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
        mask = torch.zeros((1, 64, 64), dtype=torch.float32)
        return (image, mask)

    def process(self, image_data):
        if not image_data:
            return self._empty()

        data = image_data.strip()
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]

        try:
            raw = base64.b64decode(data)
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception as exc:  # noqa: BLE001
            print(f"[WepeNerd Liquify] Failed to decode image_data: {exc}")
            return self._empty()

        arr = np.array(img).astype(np.float32) / 255.0
        rgb = arr[..., :3]
        alpha = arr[..., 3]

        image_tensor = torch.from_numpy(rgb)[None,]
        mask_tensor = torch.from_numpy(alpha)[None,]
        return (image_tensor, mask_tensor)

    @classmethod
    def IS_CHANGED(cls, image_data):
        return hashlib.sha256((image_data or "").encode("utf-8")).hexdigest()
