"""
ComfyUI-WepeNerd  —  Custom node pack by WepeNerd
===================================================
https://github.com/WepeNerd/ComfyUI-WepeNerd

Install:
  1. Clone into ComfyUI/custom_nodes/
  2. Restart ComfyUI
  3. Nodes appear under the "WepeNerd" category
"""

import math


# ================================================================== #
#  Resolution Suggest
# ================================================================== #

class WN_ResolutionSuggest:
    """
    Takes a source width/height and a target dimension, then outputs
    proportionally resized width/height snapped to a chosen divisor.

    Modes:
      - Longest Side:  scales so the longest side matches the target.
      - Shortest Side: scales so the shortest side matches the target.
      - Width:         scales so width matches the target.
      - Height:        scales so height matches the target.
      - Scale Factor:  multiplies both dimensions by target / 100  (e.g. 50 = half).
    """

    SNAP_MODES = ["round", "floor", "ceil"]
    RESIZE_MODES = ["Longest Side", "Shortest Side", "Width", "Height", "Scale Factor"]
    DIVISOR_OPTIONS = [32, 16, 8, 64]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "width":       ("INT", {"default": 1920, "min": 1, "max": 32768, "step": 1}),
                "height":      ("INT", {"default": 1080, "min": 1, "max": 32768, "step": 1}),
                "target":      ("INT", {"default": 1024, "min": 1, "max": 32768, "step": 1,
                                        "tooltip": "Target pixel size (or percentage when mode is Scale Factor)"}),
                "resize_mode": (cls.RESIZE_MODES, {"default": "Longest Side"}),
                "divisor":     (cls.DIVISOR_OPTIONS, {"default": 32}),
                "snap_mode":   (cls.SNAP_MODES, {"default": "round",
                                                  "tooltip": "How to snap to the divisor grid: round (nearest), floor (down), ceil (up)"}),
            },
        }

    RETURN_TYPES  = ("INT", "INT", "INT", "INT", "FLOAT", "STRING")
    RETURN_NAMES  = ("width", "height", "original_width", "original_height", "scale_factor", "info")
    FUNCTION      = "suggest"
    CATEGORY      = "WepeNerd/Resolution"
    OUTPUT_NODE   = False

    @staticmethod
    def _snap(value, divisor, mode):
        if mode == "floor":
            result = math.floor(value / divisor) * divisor
        elif mode == "ceil":
            result = math.ceil(value / divisor) * divisor
        else:
            result = round(value / divisor) * divisor
        return max(divisor, int(result))

    def suggest(self, width, height, target, resize_mode, divisor, snap_mode):
        aspect = width / height

        if resize_mode == "Longest Side":
            if width >= height:
                new_w = float(target)
                new_h = new_w / aspect
            else:
                new_h = float(target)
                new_w = new_h * aspect

        elif resize_mode == "Shortest Side":
            if width <= height:
                new_w = float(target)
                new_h = new_w / aspect
            else:
                new_h = float(target)
                new_w = new_h * aspect

        elif resize_mode == "Width":
            new_w = float(target)
            new_h = new_w / aspect

        elif resize_mode == "Height":
            new_h = float(target)
            new_w = new_h * aspect

        else:  # Scale Factor (target treated as percentage)
            scale = target / 100.0
            new_w = width * scale
            new_h = height * scale

        out_w = self._snap(new_w, divisor, snap_mode)
        out_h = self._snap(new_h, divisor, snap_mode)

        scale_factor = round(out_w / width, 6)

        info = (
            f"{width}\u00d7{height}  \u2192  {out_w}\u00d7{out_h}\n"
            f"Aspect: {aspect:.4f}  |  Mode: {resize_mode}\n"
            f"Divisor: {divisor}  |  Snap: {snap_mode}\n"
            f"Scale: {scale_factor:.4f}x"
        )

        return (out_w, out_h, width, height, scale_factor, info)


# ================================================================== #
#  Registration  —  add new nodes here
# ================================================================== #

NODE_CLASS_MAPPINGS = {
    "WN_ResolutionSuggest": WN_ResolutionSuggest,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_ResolutionSuggest": "Resolution Suggest (WepeNerd)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
