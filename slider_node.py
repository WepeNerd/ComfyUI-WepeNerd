"""Normalized semantic slider with calibrated FLOAT output."""

import math


def map_slider_value(normalized, low_value, center_value, high_value, curve=1.0):
    """Map -1..+1 independently across the LOW/CENTER/HIGH segments."""
    values = tuple(
        float(value)
        for value in (normalized, low_value, center_value, high_value, curve)
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Slider values must be finite numbers.")

    normalized, low_value, center_value, high_value, curve = values
    if curve <= 0.0:
        raise ValueError("Slider curve must be greater than zero.")

    normalized = max(-1.0, min(1.0, normalized))
    if normalized >= 0.0:
        return center_value + (high_value - center_value) * (normalized ** curve)
    return center_value + (low_value - center_value) * ((-normalized) ** curve)


class WN_Slider:
    """Standard -1..0..+1 control mapped to a calibrated FLOAT range."""

    CATEGORY = "WepeNerd/Utilities"
    FUNCTION = "map_value"
    RETURN_TYPES = ("FLOAT",)
    RETURN_NAMES = ("strength",)
    DESCRIPTION = (
        "A normalized LOW-to-HIGH slider that outputs a calibrated FLOAT. "
        "Connect strength to any compatible LoRA or parameter input."
    )

    @classmethod
    def INPUT_TYPES(cls):
        calibration = {
            "min": -10000.0,
            "max": 10000.0,
            "step": 0.01,
        }
        return {
            "required": {
                "normalized": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": -1.0,
                        "max": 1.0,
                        "step": 0.01,
                        "display": "slider",
                        "tooltip": "Semantic position: -1 LOW, 0 CENTER, +1 HIGH.",
                    },
                ),
                "label": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "A workflow-facing name such as Realism or Detail.",
                    },
                ),
                "low_value": (
                    "FLOAT",
                    {**calibration, "default": -1.0, "tooltip": "Output at normalized -1 (LOW)."},
                ),
                "center_value": (
                    "FLOAT",
                    {**calibration, "default": 0.0, "tooltip": "Output at normalized 0 (CENTER)."},
                ),
                "high_value": (
                    "FLOAT",
                    {**calibration, "default": 1.0, "tooltip": "Output at normalized +1 (HIGH)."},
                ),
                "curve": (
                    "FLOAT",
                    {
                        "default": 1.0,
                        "min": 0.1,
                        "max": 5.0,
                        "step": 0.01,
                        "tooltip": "1 is linear; above 1 is softer near CENTER.",
                    },
                ),
            }
        }

    def map_value(
        self,
        normalized,
        label,
        low_value,
        center_value,
        high_value,
        curve,
    ):
        del label  # Serialized for the frontend; it does not alter execution.

        strength = map_slider_value(
            normalized,
            low_value,
            center_value,
            high_value,
            curve,
        )
        return (float(strength),)


NODE_CLASS_MAPPINGS = {"WN_Slider": WN_Slider}
NODE_DISPLAY_NAME_MAPPINGS = {"WN_Slider": "Slider"}
