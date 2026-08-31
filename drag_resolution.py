"""Deterministic resolution math shared by the Drag Resolution backend."""

import math


MIN_RESOLUTION = 64
MAX_RESOLUTION = 8192
SEARCH_STEPS = 8


def parse_aspect_ratio(value):
    if not isinstance(value, str) or value == "Free":
        return None
    try:
        left, right = value.split(":", 1)
        ratio = float(left) / float(right)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return ratio if math.isfinite(ratio) and ratio > 0 else None


def sanitize_divisor(divisor):
    try:
        divisor = int(divisor)
    except (TypeError, ValueError):
        divisor = 32
    return max(1, min(MAX_RESOLUTION, divisor))


def legal_bounds(divisor):
    divisor = sanitize_divisor(divisor)
    minimum = max(divisor, math.ceil(MIN_RESOLUTION / divisor) * divisor)
    maximum = math.floor(MAX_RESOLUTION / divisor) * divisor
    return minimum, max(minimum, maximum)


def snap_dimension(value, divisor):
    divisor = sanitize_divisor(divisor)
    minimum, maximum = legal_bounds(divisor)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = minimum
    if not math.isfinite(value) or value <= 0:
        value = minimum
    # Match JavaScript Math.round for positive dimensions.
    snapped = math.floor(value / divisor + 0.5) * divisor
    return max(minimum, min(maximum, snapped))


def resolution_megapixels(width, height):
    return (width * height) / 1_000_000.0


def ratio_string(width, height):
    common = math.gcd(int(width), int(height))
    return f"{int(width) // common}:{int(height) // common}"


def sanitize_target_mp(value, fallback):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = fallback
    return value if math.isfinite(value) and value > 0 else fallback


def find_best_resolution(target_mp, target_ratio, divisor):
    divisor = sanitize_divisor(divisor)
    minimum, maximum = legal_bounds(divisor)
    max_mp = resolution_megapixels(maximum, maximum)

    try:
        target_mp = float(target_mp)
    except (TypeError, ValueError):
        target_mp = 1.0
    if not math.isfinite(target_mp) or target_mp <= 0:
        target_mp = resolution_megapixels(minimum, minimum)
    target_mp = min(target_mp, max_mp)

    try:
        target_ratio = float(target_ratio)
    except (TypeError, ValueError):
        target_ratio = 1.0
    if not math.isfinite(target_ratio) or target_ratio <= 0:
        target_ratio = 1.0

    target_pixels = target_mp * 1_000_000.0
    ideal_w = math.sqrt(target_pixels * target_ratio)
    ideal_h = math.sqrt(target_pixels / target_ratio)

    def candidate_axis(ideal):
        center = math.floor(ideal / divisor + 0.5) * divisor
        center = max(minimum, min(maximum, center))
        return sorted({
            max(minimum, min(maximum, center + offset * divisor))
            for offset in range(-SEARCH_STEPS, SEARCH_STEPS + 1)
        } | {minimum, maximum})

    best = None
    best_key = None
    for width in candidate_axis(ideal_w):
        for height in candidate_axis(ideal_h):
            area_error = abs(width * height - target_pixels) / target_pixels
            ratio_error = abs(math.log((width / height) / target_ratio))
            size_error = (
                ((width - ideal_w) / ideal_w) ** 2
                + ((height - ideal_h) / ideal_h) ** 2
            )
            score = area_error + ratio_error * 2.0 + size_error * 0.1
            key = (score, area_error, ratio_error, size_error,
                   width * height, width, height)
            if best_key is None or key < best_key:
                best_key = key
                best = (width, height)
    return best


class WN_DragResolution:
    """Interactive visual resolution picker backed by divisor-grid validation."""

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
                "target_mp":     ("FLOAT", {"default": 1.0, "min": 0.01, "max": 67.0, "step": 0.05}),
            },
        }

    RETURN_TYPES = ("INT", "INT", "STRING", "STRING")
    RETURN_NAMES = ("width", "height", "aspect_ratio", "info")
    FUNCTION = "resolve"
    CATEGORY = "WepeNerd/Resolution"
    OUTPUT_NODE = False

    def resolve(self, width, height, aspect_ratio, divisor, target_mp=1.0):
        divisor = sanitize_divisor(divisor)
        snapped_w = snap_dimension(width, divisor)
        snapped_h = snap_dimension(height, divisor)

        preset_ratio = parse_aspect_ratio(aspect_ratio)
        current_ratio = snapped_w / snapped_h if snapped_h else 1.0
        target_ratio = preset_ratio or current_ratio
        current_mp = resolution_megapixels(snapped_w, snapped_h)
        requested_mp = sanitize_target_mp(target_mp, current_mp)
        try:
            raw_w = float(width)
            raw_h = float(height)
            already_valid = (
                math.isfinite(raw_w) and math.isfinite(raw_h)
                and raw_w == snapped_w and raw_h == snapped_h
            )
        except (TypeError, ValueError):
            already_valid = False

        if preset_ratio:
            searched = find_best_resolution(requested_mp, target_ratio, divisor)
            ratio_error = abs(math.log(current_ratio / target_ratio))
            # Preserve valid dimensions already produced by the UI. A materially
            # incompatible fixed-aspect pair is corrected by the same MP search.
            if already_valid and (
                (snapped_w, snapped_h) == searched or ratio_error <= 0.03
            ):
                out_w, out_h = snapped_w, snapped_h
            else:
                out_w, out_h = searched
        elif already_valid:
            out_w, out_h = snapped_w, snapped_h
        else:
            out_w, out_h = find_best_resolution(current_mp, target_ratio, divisor)

        actual_ratio = ratio_string(out_w, out_h)
        actual_mp = resolution_megapixels(out_w, out_h)
        if preset_ratio:
            aspect_line = (
                f"Target aspect: {aspect_ratio}\n"
                f"Actual aspect: {out_w / out_h:.4f}"
            )
        else:
            aspect_line = "Aspect: Free"
        info = (
            f"{out_w}\u00d7{out_h} ({actual_ratio})\n"
            f"{actual_mp:.3f} MP\n"
            f"{aspect_line}\n"
            f"Divisor: {divisor}"
        )
        return out_w, out_h, actual_ratio, info
