import asyncio
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import torch
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError


MAX_PIXELS = 16_777_216
MAX_SOURCE_PIXELS = 67_108_864
MAX_BYTES = 64 * 1024 * 1024
ASSET_FOLDER = "wepenerd_speedpaint"
ASSET_NAME = re.compile(r"[0-9a-f]{64}\.(png|jpg|webp)\Z")


def validate_size(width, height):
    if any(type(v) is not int or not 64 <= v <= 4096 for v in (width, height)):
        raise ValueError("Speedpaint dimensions must be integers from 64 to 4096 pixels.")
    if width * height > MAX_PIXELS:
        raise ValueError("Speedpaint canvas exceeds the pixel budget.")
    return width, height


def background_rgb(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Speedpaint background must be a six-digit hex colour.")
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def asset_root():
    import folder_paths

    return Path(folder_paths.get_input_directory()) / ASSET_FOLDER


def asset_path(name):
    if not isinstance(name, str) or not ASSET_NAME.fullmatch(name):
        raise ValueError("Invalid Speedpaint asset reference.")
    root = asset_root().resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ValueError("Speedpaint asset is outside its input folder.")
    return path


def store_bytes(raw, suffix="png"):
    name = hashlib.sha256(raw).hexdigest() + "." + suffix
    path = asset_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(raw)
        try:
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return name


def store_image(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return store_bytes(stream.getvalue())


def decode_image(raw, *, colour_manage=True):
    if len(raw) > MAX_BYTES:
        raise ValueError("Speedpaint image exceeds 64 MiB.")
    with Image.open(io.BytesIO(raw)) as opened:
        if opened.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Speedpaint supports PNG, JPEG and WebP images.")
        if opened.width * opened.height > MAX_SOURCE_PIXELS:
            raise ValueError("Speedpaint source exceeds 64 megapixels.")
        source = ImageOps.exif_transpose(opened)
        profile = source.info.get("icc_profile")
        alpha = source.convert("RGBA").getchannel("A")
        if colour_manage and profile:
            profile_image = source if source.mode in {"RGB", "CMYK", "LAB", "L"} else source.convert("RGB")
            source = ImageCms.profileToProfile(
                profile_image, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile("sRGB"), outputMode="RGB",
            )
        source = source.convert("RGBA")
        source.putalpha(alpha)
        return source


def read_asset(name):
    path = asset_path(name)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Speedpaint asset exceeds 64 MiB.")
    return decode_image(path.read_bytes())


def read_composition(document):
    inline = document.get("inline")
    if inline:
        prefix = "data:image/png;base64,"
        if not isinstance(inline, str) or not inline.startswith(prefix) or len(inline) > MAX_BYTES * 4 // 3 + 128:
            raise ValueError("Invalid Speedpaint PNG data.")
        return decode_image(base64.b64decode(inline[len(prefix):], validate=True), colour_manage=False)
    return read_asset(document["asset"])


def parse_document(value):
    if not value:
        return {"v": 1, "background": "#e8e6e1", "painted": False, "crop": [0.5, 0.5]}
    document = json.loads(value) if isinstance(value, str) else value
    if not isinstance(document, dict):
        raise ValueError("Speedpaint document must be an object.")
    document = dict(document)
    if document.get("v") != 1:
        raise ValueError("Unsupported Speedpaint document version.")
    background_rgb(document.get("background", "#e8e6e1"))
    crop = document.get("crop", [0.5, 0.5])
    if not isinstance(crop, list) or len(crop) != 2 or any(
        type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in crop
    ):
        raise ValueError("Speedpaint crop position must be between 0 and 1.")
    if type(document.get("painted", False)) is not bool:
        raise ValueError("Invalid Speedpaint paint state.")
    return document


def prepare_image(document, width, height):
    size = validate_size(width, height)
    colour = background_rgb(document.get("background", "#e8e6e1"))
    painted = document.get("painted", False)
    if painted:
        source = read_composition(document)
        centering = (0.5, 0.5)
    elif document.get("source"):
        source = read_asset(document["source"])
        centering = tuple(document.get("crop", [0.5, 0.5]))
    else:
        return Image.new("RGB", size, colour)
    prepared = ImageOps.fit(source, size, method=Image.Resampling.LANCZOS, centering=centering)
    background = Image.new("RGBA", size, (*colour, 255))
    return Image.alpha_composite(background, prepared).convert("RGB")


def prepare_document(document, width, height):
    document = parse_document(document)
    prepared = prepare_image(document, width, height)
    return {**{k: v for k, v in document.items() if k != "inline"},
            "asset": store_image(prepared), "width": width, "height": height}


def import_document(raw, width, height, background):
    validate_size(width, height)
    background_rgb(background)
    source = decode_image(raw)
    with Image.open(io.BytesIO(raw)) as image:
        suffix = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[image.format]
    document = {"v": 1, "source": store_bytes(raw, suffix), "background": background,
                "painted": False, "crop": [0.5, 0.5], "source_width": source.width, "source_height": source.height}
    return prepare_document(document, width, height)


def commit_document(document):
    document = parse_document(document)
    validate_size(document["width"], document["height"])
    image = read_composition(document)
    if image.size != (document["width"], document["height"]):
        raise ValueError("Speedpaint composition does not match its editing dimensions.")
    if image.getchannel("A").getextrema() != (255, 255):
        raise ValueError("Speedpaint composition must be opaque.")
    return {**{k: v for k, v in document.items() if k != "inline"}, "asset": store_image(image.convert("RGB"))}


class WN_Speedpaint:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "width": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 1}),
            "height": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 1}),
            "document": ("STRING", {"default": "", "multiline": False, "socketless": True}),
        }}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "render"
    CATEGORY = "WepeNerd/Image"
    DESCRIPTION = "Sketch on a blank canvas or a loaded image, then send the painting to VAE Encode."

    def render(self, width, height, document=""):
        prepared = prepare_image(parse_document(document), width, height)
        asset = store_image(prepared)
        tensor = torch.from_numpy(np.asarray(prepared).astype(np.float32) / 255.0).unsqueeze(0)
        return {"ui": {"speedpaint": [{"document": document, "asset": asset, "width": width, "height": height}]},
                "result": (tensor,)}


def register_speedpaint_routes():
    from aiohttp import web
    from server import PromptServer

    instance = PromptServer.instance
    if instance is None:
        return

    @instance.routes.post("/wepenerd/speedpaint/{operation}")
    async def speedpaint_request(request):
        try:
            operation = request.match_info["operation"]
            if request.content_length is not None and request.content_length > MAX_BYTES * 2:
                raise ValueError("Speedpaint request is too large.")
            if operation == "import":
                reader = await request.multipart()
                fields = {}
                total = 0
                async for part in reader:
                    data = bytearray()
                    while chunk := await part.read_chunk():
                        data.extend(chunk)
                        total += len(chunk)
                        if len(data) > MAX_BYTES or total > MAX_BYTES + 1024:
                            raise ValueError("Speedpaint upload exceeds 64 MiB.")
                    if part.name not in {"image", "width", "height", "background"} or part.name in fields:
                        raise ValueError("Invalid Speedpaint upload field.")
                    if part.name != "image" and len(data) > 32:
                        raise ValueError("Invalid Speedpaint upload field length.")
                    fields[part.name] = bytes(data) if part.name == "image" else data.decode("utf-8")
                result = await asyncio.to_thread(import_document, fields["image"], int(fields["width"]),
                                                 int(fields["height"]), fields["background"])
            elif operation in {"prepare", "commit"}:
                raw = bytearray()
                async for chunk in request.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES * 2:
                        raise ValueError("Speedpaint request is too large.")
                body = json.loads(raw)
                if operation == "prepare":
                    result = await asyncio.to_thread(prepare_document, body["document"], body["width"], body["height"])
                else:
                    result = await asyncio.to_thread(commit_document, body["document"])
            else:
                raise ValueError("Unknown Speedpaint operation.")
            return web.json_response(result)
        except (ValueError, KeyError, TypeError, OSError, UnidentifiedImageError,
                Image.DecompressionBombError, ImageCms.PyCMSError) as error:
            return web.json_response({"error": f"Speedpaint: {error}"}, status=400)
