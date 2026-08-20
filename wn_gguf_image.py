"""In-memory image encoding helpers for Comfy IMAGE batches and video frames."""

from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image


def _as_numpy(value) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def image_batch_to_numpy(image_tensor) -> np.ndarray:
    if image_tensor is None:
        raise ValueError("IMAGE input is missing")
    value = _as_numpy(image_tensor)
    if value.ndim == 3:
        value = value[None, ...]
    if value.ndim != 4 or value.shape[-1] not in (3, 4):
        raise ValueError(f"Expected IMAGE shape [B,H,W,C], got {value.shape}")
    if value.shape[0] < 1 or value.shape[1] < 1 or value.shape[2] < 1:
        raise ValueError("IMAGE batch is empty")
    return value


def encode_single_image(
    image,
    max_edge: int = 1024,
    image_format: str = "JPEG",
    jpeg_quality: int = 90,
) -> str:
    value = _as_numpy(image)
    if value.ndim != 3 or value.shape[-1] not in (3, 4):
        raise ValueError(f"Expected one image shaped [H,W,C], got {value.shape}")
    if max_edge < 64:
        raise ValueError("image_max_edge must be at least 64")
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("jpeg_quality must be between 1 and 100")

    value = (np.clip(value, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    pil = Image.fromarray(value)
    longest = max(pil.size)
    if longest > max_edge:
        scale = max_edge / longest
        size = (max(1, round(pil.width * scale)), max(1, round(pil.height * scale)))
        pil = pil.resize(size, Image.Resampling.LANCZOS)

    normalized_format = image_format.upper()
    save_kwargs = {}
    if normalized_format in ("JPG", "JPEG"):
        normalized_format = "JPEG"
        if pil.mode == "RGBA":
            background = Image.new("RGB", pil.size, "white")
            background.paste(pil, mask=pil.getchannel("A"))
            pil = background
        elif pil.mode != "RGB":
            pil = pil.convert("RGB")
        save_kwargs = {"quality": int(jpeg_quality), "optimize": True}
        mime = "image/jpeg"
    elif normalized_format == "PNG":
        mime = "image/png"
    else:
        raise ValueError("image_format must be JPEG or PNG")

    buffer = io.BytesIO()
    pil.save(buffer, format=normalized_format, **save_kwargs)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def encode_image_batch(
    image_tensor,
    max_edge: int = 1024,
    image_format: str = "JPEG",
    jpeg_quality: int = 90,
) -> list[str]:
    batch = image_batch_to_numpy(image_tensor)
    return [
        encode_single_image(image, max_edge, image_format, jpeg_quality)
        for image in batch
    ]


def comfy_image_to_data_url(
    image_tensor,
    fmt: str = "JPEG",
    max_edge: int = 1024,
    jpeg_quality: int = 90,
) -> str:
    """Backward-compatible single-image helper that now rejects silent truncation."""
    batch = image_batch_to_numpy(image_tensor)
    if batch.shape[0] != 1:
        raise ValueError(
            f"General GGUF generation accepts one image, but received a batch of {batch.shape[0]}. "
            "Use GGUF Caption Image for image batches."
        )
    return encode_single_image(batch[0], max_edge, fmt, jpeg_quality)
