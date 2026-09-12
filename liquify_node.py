"""Editable push-warp strokes, rendered from the original at source resolution."""

import base64
import binascii
import hashlib
import io
import json
import math

import numpy as np
import torch
from PIL import Image, ImageCms, ImageOps
from comfy.model_management import throw_exception_if_processing_interrupted

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 64 * 1024 * 1024
MAX_POINTS = 100_000
PREVIEW_EDGE = 1536


def parse_document(value):
    if not isinstance(value, str) or len(value) > 96 * 1024 * 1024:
        raise ValueError("Liquify document is invalid or exceeds 96 MiB.")
    if not value or not value.lstrip().startswith("{"):
        return {"v": 2, "source": value, "strokes": [], "redo": []}
    document = json.loads(value)
    if document.get("v") != 2:
        raise ValueError("Unsupported Liquify document version. Update ComfyUI-WepeNerd.")
    total = 0
    for key in ("strokes", "redo"):
        strokes = document.get(key, [])
        if not isinstance(strokes, list) or len(strokes) > 2000:
            raise ValueError("Liquify supports up to 2000 saved strokes.")
        for stroke in strokes:
            if not isinstance(stroke, dict):
                raise ValueError("Invalid Liquify stroke.")
            radius, strength = stroke.get("radius"), stroke.get("strength")
            if (type(radius) not in (float, int) or not math.isfinite(radius)
                    or not 0.0001 <= radius <= 1
                    or type(strength) not in (float, int) or not math.isfinite(strength)
                    or not 0 <= strength <= 1):
                raise ValueError("Invalid Liquify brush radius or strength.")
            points = stroke.get("points")
            if not isinstance(points, list) or not points:
                raise ValueError("Liquify stroke has no points.")
            total += len(points)
            if total > MAX_POINTS:
                raise ValueError("Liquify stroke history exceeds 100,000 points.")
            for point in points:
                if (not isinstance(point, list) or len(point) != 2 or any(
                        type(v) not in (float, int) or not math.isfinite(v) or not -1 <= v <= 2
                        for v in point)):
                    raise ValueError("Invalid Liquify stroke coordinates.")
    return document


def decode_source(value):
    if not isinstance(value, str) or len(value) > MAX_BYTES * 4 // 3 + 128:
        raise ValueError("Liquify source exceeds 64 MiB.")
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or not header.endswith(";base64"):
            raise ValueError("Liquify source must contain a base64 image.")
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > MAX_BYTES:
            raise ValueError("Liquify source exceeds 64 MiB.")
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Liquify supports PNG, JPEG and WebP images.")
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError("Liquify source exceeds 64 megapixels.")
            source = ImageOps.exif_transpose(opened)
            alpha = source.convert("RGBA").getchannel("A")
            profile = source.info.get("icc_profile")
            if profile:
                colour = source if source.mode in {"RGB", "CMYK", "LAB", "L"} else source.convert("RGB")
                source = ImageCms.profileToProfile(colour, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                                  ImageCms.createProfile("sRGB"), outputMode="RGB")
            source = source.convert("RGBA")
            source.putalpha(alpha)
            return torch.from_numpy(np.array(source).astype(np.float32) / 255.0)[None]
    except (binascii.Error, OSError) as exc:
        raise ValueError("Liquify could not read the saved image. Load a valid PNG, JPEG or WebP.") from exc


def brush_dabs(strokes, width, height):
    longest = max(width, height)
    dabs = []
    for stroke in strokes:
        radius = stroke["radius"] * longest
        points = stroke["points"]
        for previous, point in zip(points, points[1:]):
            dx = (point[0] - previous[0]) * width
            dy = (point[1] - previous[1]) * height
            # Avoid an extra dab from roundoff when scaling the preview geometry.
            steps = max(1, math.ceil(math.hypot(dx, dy) / (radius * 0.25) - 1e-9))
            if len(dabs) + steps > 500_000:
                raise ValueError("Liquify stroke history is too large to render. Split the edits across nodes.")
            for step in range(1, steps + 1):
                dabs.append((previous[0] * width + dx * step / steps,
                             previous[1] * height + dy * step / steps,
                             radius, dx / steps * stroke["strength"], dy / steps * stroke["strength"]))
    return dabs


def warp_image(image, strokes):
    """Replay on short row strips; do not retain a full-resolution displacement map."""
    if not strokes:
        return image
    batch, height, width, channels = image.shape
    dabs = brush_dabs(strokes, width, height)
    if not dabs:
        return image
    source = image.detach().to(device="cpu", dtype=torch.float32).numpy()
    output = source.copy()
    for top in range(0, height, 128):
        throw_exception_if_processing_interrupted()
        bottom = min(height, top + 128)
        visible = [dab for dab in dabs if dab[1] + dab[2] > top and dab[1] - dab[2] < bottom
                   and dab[0] + dab[2] > 0 and dab[0] - dab[2] < width and (dab[3] or dab[4])]
        if not visible:
            continue
        left = max(0, min(math.floor(cx - radius) for cx, cy, radius, dx, dy in visible))
        right = min(width, max(math.ceil(cx + radius) for cx, cy, radius, dx, dy in visible))
        yy, xx = np.mgrid[top:bottom, left:right].astype(np.float32)
        offset_x = np.zeros_like(xx)
        offset_y = np.zeros_like(yy)
        for cx, cy, radius, dx, dy in visible:
            x0, x1 = max(0, math.floor(cx - radius)), min(width, math.ceil(cx + radius))
            y0, y1 = max(top, math.floor(cy - radius)), min(bottom, math.ceil(cy + radius))
            if x0 >= x1 or y0 >= y1:
                continue
            region = np.s_[y0 - top:y1 - top, x0 - left:x1 - left]
            distance = np.sqrt((xx[region] - cx) ** 2 + (yy[region] - cy) ** 2)
            weight = np.clip(1 - distance / radius, 0, 1)
            weight = weight * weight * (3 - 2 * weight)
            offset_x[region] -= dx * weight
            offset_y[region] -= dy * weight
        sx = np.clip(xx + offset_x, 0, width - 1)
        sy = np.clip(yy + offset_y, 0, height - 1)
        x0, y0 = sx.astype(np.int32), sy.astype(np.int32)
        x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
        fx, fy = (sx - x0).astype(np.float32)[..., None], (sy - y0).astype(np.float32)[..., None]
        for index in range(batch):
            pixels = source[index]
            upper = pixels[y0, x0] + (pixels[y0, x1] - pixels[y0, x0]) * fx
            lower = pixels[y1, x0] + (pixels[y1, x1] - pixels[y1, x0]) * fx
            output[index, top:bottom, left:right] = upper + (lower - upper) * fy
    return torch.from_numpy(output)


def source_preview(image):
    height, width = image.shape[1:3]
    scale = min(1, PREVIEW_EDGE / max(width, height))
    preview_tensor = image[:1].detach().movedim(-1, 1).to(device="cpu", dtype=torch.float32)
    if scale < 1:
        preview_tensor = torch.nn.functional.interpolate(
            preview_tensor, size=(max(1, round(height * scale)), max(1, round(width * scale))),
            mode="bilinear", align_corners=False, antialias=True)
    pixels = np.clip(preview_tensor[0].movedim(0, -1).numpy() * 255, 0, 255).astype(np.uint8)
    preview = Image.fromarray(pixels)
    preview.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    preview.save(stream, format="PNG")
    return {"data": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode(),
            "width": image.shape[2], "height": image.shape[1], "batch": image.shape[0]}


class WN_LiquifyImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image_data": ("STRING", {"default": "", "multiline": False})},
                "optional": {"image": ("IMAGE",)}}

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "process"
    CATEGORY = "WepeNerd/Image"

    def process(self, image_data, image=None):
        document = parse_document(image_data)
        connected = image is not None
        if not connected:
            if document.get("source_mode") == "input":
                raise ValueError("Liquify needs its IMAGE connection restored, or a new image loaded in the editor.")
            if not document.get("source"):
                return (torch.zeros((1, 64, 64, 3)), torch.zeros((1, 64, 64)))
            image = decode_source(document["source"])
        if image.ndim != 4 or image.shape[-1] not in (3, 4) or min(image.shape[:3]) < 1:
            raise ValueError("Liquify expects an IMAGE batch shaped [batch, height, width, 3 or 4].")
        if image.shape[1] * image.shape[2] > MAX_PIXELS:
            raise ValueError("Liquify source exceeds 64 megapixels.")
        result = warp_image(image, document.get("strokes", []))
        rgb = result[..., :3]
        alpha = result[..., 3] if result.shape[-1] == 4 else torch.ones_like(result[..., 0])
        if connected:
            return {"ui": {"liquify_source": [source_preview(image)]}, "result": (rgb, alpha)}
        return (rgb, alpha)

    @classmethod
    def IS_CHANGED(cls, image_data, image=None):
        # ComfyUI tracks the IMAGE connection separately from this widget value.
        return hashlib.sha256((image_data or "").encode("utf-8")).hexdigest()
