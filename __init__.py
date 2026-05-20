"""
ComfyUI-WepeNerd  —  Custom node pack by WepeNerd
===================================================
https://github.com/WepeNerd/ComfyUI-WepeNerd
"""

import math

WEB_DIRECTORY = "./js"


# ================================================================== #
#  Resolution Suggest  (text-based, original node)
# ================================================================== #

class WN_ResolutionSuggest:
    """Proportionally resize width/height, snapped to a divisor grid."""

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

    RETURN_TYPES  = ("INT", "INT", "INT", "INT", "FLOAT", "STRING", "STRING")
    RETURN_NAMES  = ("width", "height", "original_width", "original_height", "scale_factor", "aspect_ratio", "info")
    FUNCTION      = "suggest"
    CATEGORY      = "WepeNerd/Resolution"

    @staticmethod
    def _snap(value, divisor, mode):
        if mode == "floor":
            result = math.floor(value / divisor) * divisor
        elif mode == "ceil":
            result = math.ceil(value / divisor) * divisor
        else:
            result = round(value / divisor) * divisor
        return max(divisor, int(result))

    @staticmethod
    def _ratio(w, h):
        g = math.gcd(w, h)
        return f"{w // g}:{h // g}"

    def suggest(self, width, height, target, resize_mode, divisor, snap_mode):
        aspect = width / height

        if resize_mode == "Longest Side":
            if width >= height:
                new_w, new_h = float(target), float(target) / aspect
            else:
                new_h, new_w = float(target), float(target) * aspect
        elif resize_mode == "Shortest Side":
            if width <= height:
                new_w, new_h = float(target), float(target) / aspect
            else:
                new_h, new_w = float(target), float(target) * aspect
        elif resize_mode == "Width":
            new_w, new_h = float(target), float(target) / aspect
        elif resize_mode == "Height":
            new_h, new_w = float(target), float(target) * aspect
        else:
            scale = target / 100.0
            new_w, new_h = width * scale, height * scale

        out_w = self._snap(new_w, divisor, snap_mode)
        out_h = self._snap(new_h, divisor, snap_mode)
        scale_factor = round(out_w / width, 6)
        src_ratio = self._ratio(width, height)
        out_ratio = self._ratio(out_w, out_h)

        info = (
            f"{width}\u00d7{height} ({src_ratio})  \u2192  {out_w}\u00d7{out_h} ({out_ratio})\n"
            f"Aspect: {aspect:.4f}  |  Mode: {resize_mode}\n"
            f"Divisor: {divisor}  |  Snap: {snap_mode}\n"
            f"Scale: {scale_factor:.4f}x"
        )
        return (out_w, out_h, width, height, scale_factor, out_ratio, info)


# ================================================================== #
#  Drag Resolution  (visual interactive node)
# ================================================================== #

class WN_DragResolution:
    """
    Interactive visual resolution picker.
    Drag a box to set dimensions snapped to the divisor grid.
    """

    ASPECT_RATIOS = ["Free", "1:1", "16:9", "9:16", "4:3", "3:4",
                     "3:2", "2:3", "21:9", "9:21", "5:4", "4:5"]
    DIVISOR_OPTIONS = [32, 16, 8, 64]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "width":        ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 32}),
                "height":       ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 32}),
                "aspect_ratio": (cls.ASPECT_RATIOS, {"default": "Free"}),
                "divisor":      (cls.DIVISOR_OPTIONS, {"default": 32}),
            },
        }

    RETURN_TYPES  = ("INT", "INT", "STRING", "STRING")
    RETURN_NAMES  = ("width", "height", "aspect_ratio", "info")
    FUNCTION      = "resolve"
    CATEGORY      = "WepeNerd/Resolution"
    OUTPUT_NODE   = False

    def resolve(self, width, height, aspect_ratio, divisor):
        # Snap to divisor
        w = max(divisor, round(width / divisor) * divisor)
        h = max(divisor, round(height / divisor) * divisor)

        g = math.gcd(w, h)
        ratio_str = f"{w // g}:{h // g}"

        info = (
            f"{w}\u00d7{h} ({ratio_str})\n"
            f"Divisor: {divisor}"
        )
        return (w, h, ratio_str, info)


# ================================================================== #
#  Registration
# ================================================================== #

NODE_CLASS_MAPPINGS = {
    "WN_ResolutionSuggest": WN_ResolutionSuggest,
    "WN_DragResolution":    WN_DragResolution,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_ResolutionSuggest": "Resolution Suggest (WepeNerd)",
    "WN_DragResolution":    "Drag Resolution (WepeNerd)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
