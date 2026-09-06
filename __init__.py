"""
ComfyUI-WepeNerd  —  Custom node pack by WepeNerd
===================================================
https://github.com/WepeNerd/ComfyUI-WepeNerd
"""

import math
import os

from .drag_resolution import WN_DragResolution
from .liquify_node import WN_LiquifyImage
from .slider_node import WN_Slider
from .masked_lora_node import WepeNerdLoadLoraMasked, WN_MaskedLoraSnapshot
from .video_frame_count_node import WN_VideoExactFramesFPS
from .wn_gguf_nodes import (
    NODE_CLASS_MAPPINGS as GGUF_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as GGUF_NODE_DISPLAY_NAME_MAPPINGS,
)

WEB_DIRECTORY = "./js"
_WN_OBJ_MESH_CACHE = {}
_WN_OBJ_MESH_CACHE_LIMIT = 8


def _import_core_render_dependencies():
    missing = []

    try:
        import numpy as np
    except Exception:
        np = None
        missing.append("numpy")

    try:
        import torch
    except Exception:
        torch = None
        missing.append("torch")

    try:
        from PIL import Image
    except Exception:
        Image = None
        missing.append("Pillow")

    if missing:
        joined = ", ".join(missing)
        install_hint = "Install this node pack's requirements.txt and restart ComfyUI."
        if "torch" in missing:
            install_hint += " Torch is expected from the ComfyUI Python environment."
        raise ImportError(
            f"3D Product Placement requires {joined}. "
            f"{install_hint}"
        )

    return np, torch, Image


def _import_3d_dependencies():
    missing = []

    try:
        import trimesh
    except Exception:
        trimesh = None
        missing.append("trimesh")

    try:
        import pyrender
    except Exception:
        pyrender = None
        missing.append("pyrender")

    if missing:
        joined = ", ".join(missing)
        raise ImportError(
            f"3D Product Placement requires {joined}. "
            "Install this node pack's requirements.txt and restart ComfyUI."
        )

    return trimesh, pyrender


def _rotation_matrix(rx, ry, rz):
    import numpy as np

    rx = math.radians(rx)
    ry = math.radians(ry)
    rz = math.radians(rz)

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    mx = np.array(
        [[1, 0, 0, 0], [0, cx, -sx, 0], [0, sx, cx, 0], [0, 0, 0, 1]],
        dtype=np.float64,
    )
    my = np.array(
        [[cy, 0, sy, 0], [0, 1, 0, 0], [-sy, 0, cy, 0], [0, 0, 0, 1]],
        dtype=np.float64,
    )
    mz = np.array(
        [[cz, -sz, 0, 0], [sz, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        dtype=np.float64,
    )

    return mz @ my @ mx


def _normalize_trimesh(mesh, trimesh):
    import numpy as np

    if isinstance(mesh, trimesh.Scene):
        geometries = [
            geometry
            for geometry in mesh.geometry.values()
            if hasattr(geometry, "vertices") and len(geometry.vertices) > 0
        ]
        if not geometries:
            raise ValueError("OBJ file contains no renderable geometry.")
        mesh = trimesh.util.concatenate(geometries)

    if not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
        raise ValueError("OBJ file contains no vertices.")
    if not hasattr(mesh, "faces") or len(mesh.faces) == 0:
        raise ValueError("OBJ file contains no faces.")

    mesh = mesh.copy()
    mesh.vertices -= mesh.bounding_box.centroid
    extents = mesh.extents
    max_extent = float(np.max(extents)) if extents is not None else 0.0
    if max_extent <= 0.0:
        raise ValueError("OBJ file has invalid zero-size geometry.")

    mesh.apply_scale(1.0 / max_extent)
    return mesh


def _load_normalized_mesh_cached(model_path, trimesh):
    stat = os.stat(model_path)
    cache_key = (model_path, stat.st_mtime_ns, stat.st_size)
    cached = _WN_OBJ_MESH_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()

    try:
        loaded_mesh = trimesh.load(model_path, force="scene", process=False)
        mesh = _normalize_trimesh(loaded_mesh, trimesh)
    except Exception as exc:
        raise ValueError(f"Could not load OBJ geometry from '{model_path}': {exc}") from exc

    _WN_OBJ_MESH_CACHE[cache_key] = mesh.copy()
    while len(_WN_OBJ_MESH_CACHE) > _WN_OBJ_MESH_CACHE_LIMIT:
        _WN_OBJ_MESH_CACHE.pop(next(iter(_WN_OBJ_MESH_CACHE)))

    return mesh


def _look_at(eye, target=(0.0, 0.0, 0.0), up=(0.0, 1.0, 0.0)):
    import numpy as np

    eye = np.array(eye, dtype=np.float64)
    target = np.array(target, dtype=np.float64)
    up = np.array(up, dtype=np.float64)

    forward = target - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    true_up = np.cross(right, forward)

    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, 0] = right
    matrix[:3, 1] = true_up
    matrix[:3, 2] = -forward
    matrix[:3, 3] = eye
    return matrix


def _resolve_obj_path(obj_path):
    if not obj_path:
        raise ValueError("Enter an OBJ path before queueing.")

    real_path = os.path.realpath(os.path.expanduser(obj_path))

    if not real_path.lower().endswith(".obj"):
        raise ValueError("Only .obj files are supported in v1.")
    if not os.path.isfile(real_path):
        raise ValueError(f"OBJ file not found: {obj_path}")

    return real_path


def _tensor_image_to_pil(image, np, Image):
    array = image.detach().cpu().numpy() if hasattr(image, "detach") else np.asarray(image)
    if array.ndim == 4:
        array = array[0]
    if array.ndim != 3 or array.shape[-1] < 3:
        raise ValueError("Expected an IMAGE tensor shaped [B,H,W,3].")

    array = np.clip(array[..., :3], 0.0, 1.0)
    return Image.fromarray((array * 255.0).astype(np.uint8), mode="RGB")


def _data_url_to_pil(data_url, Image):
    import base64
    import io

    if not data_url or not isinstance(data_url, str):
        return None
    if not data_url.startswith("data:image/png;base64,"):
        return None

    encoded = data_url.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
        return Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception:
        return None


def _render_clay_obj_rgba(
    model_path,
    width,
    height,
    scale,
    rotate_x,
    rotate_y,
    rotate_z,
    x_offset=0.0,
    y_offset=0.0,
    z_offset=0.0,
    camera_zoom=1.0,
    light_yaw=45.0,
    light_pitch=45.0,
    light_intensity=3.0,
    wireframe_overlay=False,
):
    np, _torch, _Image = _import_core_render_dependencies()
    trimesh, pyrender = _import_3d_dependencies()
    mesh = _load_normalized_mesh_cached(model_path, trimesh)

    material = pyrender.MetallicRoughnessMaterial(
        baseColorFactor=[0.58, 0.58, 0.56, 1.0],
        metallicFactor=0.0,
        roughnessFactor=0.95,
        alphaMode="OPAQUE",
        doubleSided=True,
    )
    render_mesh = pyrender.Mesh.from_trimesh(mesh, material=material, smooth=True)
    wireframe_mesh = None
    if wireframe_overlay:
        wireframe_material = pyrender.MetallicRoughnessMaterial(
            baseColorFactor=[0.04, 0.04, 0.04, 1.0],
            metallicFactor=0.0,
            roughnessFactor=1.0,
            alphaMode="OPAQUE",
            doubleSided=True,
        )
        wireframe_mesh = pyrender.Mesh.from_trimesh(mesh, material=wireframe_material, wireframe=True)

    scene = pyrender.Scene(
        bg_color=[0.0, 0.0, 0.0, 0.0],
        ambient_light=[0.35, 0.35, 0.35],
    )

    scale_matrix = np.diag([float(scale), float(scale), float(scale), 1.0])
    translation_matrix = np.eye(4, dtype=np.float64)
    translation_matrix[:3, 3] = [float(x_offset), -float(y_offset), float(z_offset)]
    transform = translation_matrix @ _rotation_matrix(rotate_x, rotate_y, rotate_z) @ scale_matrix
    scene.add(render_mesh, pose=transform)
    if wireframe_mesh is not None:
        scene.add(wireframe_mesh, pose=transform)

    safe_zoom = max(0.05, float(camera_zoom))
    camera = pyrender.OrthographicCamera(
        xmag=max(1.0, width / (2.0 * safe_zoom)),
        ymag=max(1.0, height / (2.0 * safe_zoom)),
        znear=0.01,
        zfar=100000.0,
    )
    camera_distance = max(width, height, float(scale) * 8.0, 1000.0)
    scene.add(camera, pose=_look_at([0.0, 0.0, camera_distance], [0.0, 0.0, 0.0]))

    yaw = math.radians(float(light_yaw))
    pitch = math.radians(float(light_pitch))
    direction = np.array(
        [
            math.cos(pitch) * math.sin(yaw),
            math.sin(pitch),
            math.cos(pitch) * math.cos(yaw),
        ],
        dtype=np.float64,
    )
    light = pyrender.DirectionalLight(
        color=np.ones(3),
        intensity=max(0.0, float(light_intensity)),
    )
    scene.add(light, pose=_look_at(direction * camera_distance, [0.0, 0.0, 0.0]))

    renderer = None
    try:
        renderer = pyrender.OffscreenRenderer(viewport_width=width, viewport_height=height)
        flags = pyrender.RenderFlags.RGBA | pyrender.RenderFlags.SKIP_CULL_FACES
        color, depth = renderer.render(scene, flags=flags)
    except Exception as exc:
        raise RuntimeError(
            "3D Product Placement could not render with pyrender. "
            "On headless systems you may need EGL/OSMesa or a working OpenGL context."
        ) from exc
    finally:
        if renderer is not None:
            renderer.delete()

    color = np.asarray(color)
    depth_array = np.asarray(depth)
    depth_mask = (np.isfinite(depth_array) & (depth_array > 0)).astype(np.uint8) * 255

    if color.ndim != 3 or color.shape[-1] not in (3, 4):
        raise RuntimeError("3D Product Placement received an unexpected renderer output shape.")

    if color.shape[-1] == 3:
        color = np.dstack([color, depth_mask])
    else:
        color = color.copy()
        color[..., 3] = np.maximum(color[..., 3], depth_mask)

    if not np.any(color[..., 3]):
        raise RuntimeError(
            "3D Product Placement rendered no visible object. "
            "Try increasing scale, resetting offsets, or checking that the OBJ contains faces."
        )

    return color


def _register_3d_routes():
    try:
        from aiohttp import web
        from server import PromptServer
        import folder_paths
    except Exception:
        return

    instance = getattr(PromptServer, "instance", None)
    if instance is None or getattr(instance, "_wn_3d_routes_registered", False):
        return

    @instance.routes.get("/wepenerd/3d_product_placement/obj")
    async def serve_3d_product_placement_obj(request):
        obj_path = request.query.get("path", "")
        if not obj_path:
            return web.Response(status=400, text="Missing obj path")

        real_path = os.path.realpath(os.path.expanduser(obj_path))

        if not real_path.lower().endswith(".obj"):
            return web.Response(status=400, text="Only .obj files are allowed")
        if not os.path.isfile(real_path):
            return web.Response(status=404, text="OBJ not found")

        # TODO: restrict this local-preview route to approved 3D folders before
        # recommending use on a public or --listen ComfyUI server.
        return web.FileResponse(real_path)

    @instance.routes.post("/wepenerd/3d_product_placement/upload_obj")
    async def upload_3d_product_placement_obj(request):
        post = await request.post()
        upload = post.get("file")

        if upload is None or not getattr(upload, "file", None):
            return web.Response(status=400, text="Missing OBJ file")

        filename = os.path.basename(getattr(upload, "filename", "") or "")
        if not filename:
            return web.Response(status=400, text="Missing filename")
        if not filename.lower().endswith(".obj"):
            return web.Response(status=400, text="Only .obj files are allowed")

        upload_root = os.path.realpath(os.path.join(folder_paths.input_directory, "3d"))
        os.makedirs(upload_root, exist_ok=True)

        base, ext = os.path.splitext(filename)
        base = "".join(ch if ch.isalnum() or ch in (" ", "-", "_", ".") else "_" for ch in base).strip()
        if not base:
            base = "model"

        safe_name = f"{base}{ext.lower()}"
        target_path = os.path.realpath(os.path.join(upload_root, safe_name))
        if os.path.commonpath([upload_root, target_path]) != upload_root:
            return web.Response(status=400, text="Invalid OBJ filename")

        index = 1
        while os.path.exists(target_path):
            safe_name = f"{base} ({index}){ext.lower()}"
            target_path = os.path.realpath(os.path.join(upload_root, safe_name))
            index += 1

        with open(target_path, "wb") as output_file:
            output_file.write(upload.file.read())

        return web.json_response({
            "name": safe_name,
            "path": target_path,
            "subfolder": "3d",
            "type": "input",
        })

    instance._wn_3d_routes_registered = True


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
#  3D Product Placement  (interactive clay OBJ placement guide)
# ================================================================== #

class WN_LoadOBJ:
    """Load a Wavefront OBJ model and provide a clay preview image."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "obj_path": ("STRING", {"default": "", "multiline": False}),
                "preview_size": ("INT", {"default": 512, "min": 128, "max": 2048, "step": 64}),
                "preview_scale": ("FLOAT", {"default": 320.0, "min": 1.0, "max": 2048.0, "step": 1.0}),
                "rotate_x": ("FLOAT", {"default": 0.0, "min": -360.0, "max": 360.0, "step": 1.0}),
                "rotate_y": ("FLOAT", {"default": 30.0, "min": -360.0, "max": 360.0, "step": 1.0}),
                "rotate_z": ("FLOAT", {"default": 0.0, "min": -360.0, "max": 360.0, "step": 1.0}),
                "light_yaw": ("FLOAT", {"default": 45.0, "min": -180.0, "max": 180.0, "step": 1.0}),
                "light_pitch": ("FLOAT", {"default": 45.0, "min": -90.0, "max": 90.0, "step": 1.0}),
                "light_intensity": ("FLOAT", {"default": 3.0, "min": 0.0, "max": 20.0, "step": 0.1}),
            }
        }

    RETURN_TYPES = ("WN_OBJ3D", "IMAGE", "MASK")
    RETURN_NAMES = ("obj_model", "preview", "preview_mask")
    FUNCTION = "load"
    CATEGORY = "WepeNerd/3D"
    OUTPUT_NODE = False

    def load(
        self,
        obj_path,
        preview_size,
        preview_scale,
        rotate_x,
        rotate_y,
        rotate_z,
        light_yaw,
        light_pitch,
        light_intensity,
    ):
        np, torch, _Image = _import_core_render_dependencies()
        model_path = _resolve_obj_path(obj_path)
        size = int(preview_size)

        color = np.asarray(
            _render_clay_obj_rgba(
                model_path,
                size,
                size,
                preview_scale,
                rotate_x,
                rotate_y,
                rotate_z,
                camera_zoom=1.0,
                light_yaw=light_yaw,
                light_pitch=light_pitch,
                light_intensity=light_intensity,
            ),
            dtype=np.float32,
        ) / 255.0
        alpha = np.clip(color[..., 3:4], 0.0, 1.0)
        preview_bg = np.full((size, size, 3), 0.18, dtype=np.float32)
        preview = color[..., :3] * alpha + preview_bg * (1.0 - alpha)
        mask = alpha[..., 0]

        obj_model = {
            "path": model_path,
            "source_path": obj_path,
        }
        return (
            obj_model,
            torch.from_numpy(np.clip(preview, 0.0, 1.0)).unsqueeze(0),
            torch.from_numpy(mask).unsqueeze(0),
        )


class WN_3DProductPlacement:
    """Render a clay OBJ placement guide over a selected background."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "background_image": ("IMAGE",),
                "obj_model": ("WN_OBJ3D",),

                "x_offset": ("FLOAT", {"default": 0.0, "min": -4096.0, "max": 4096.0, "step": 1.0}),
                "y_offset": ("FLOAT", {"default": 0.0, "min": -4096.0, "max": 4096.0, "step": 1.0}),
                "z_offset": ("FLOAT", {"default": 0.0, "min": -4096.0, "max": 4096.0, "step": 1.0}),

                "scale": ("FLOAT", {"default": 300.0, "min": 1.0, "max": 8192.0, "step": 1.0}),

                "rotate_x": ("FLOAT", {"default": 0.0, "min": -360.0, "max": 360.0, "step": 1.0}),
                "rotate_y": ("FLOAT", {"default": 0.0, "min": -360.0, "max": 360.0, "step": 1.0}),
                "rotate_z": ("FLOAT", {"default": 0.0, "min": -360.0, "max": 360.0, "step": 1.0}),

                "camera_zoom": ("FLOAT", {"default": 1.0, "min": 0.05, "max": 10.0, "step": 0.01}),

                "light_yaw": ("FLOAT", {"default": 45.0, "min": -180.0, "max": 180.0, "step": 1.0}),
                "light_pitch": ("FLOAT", {"default": 45.0, "min": -90.0, "max": 90.0, "step": 1.0}),
                "light_intensity": ("FLOAT", {"default": 3.0, "min": 0.0, "max": 20.0, "step": 0.1}),

                "wireframe_overlay": ("BOOLEAN", {"default": False}),
                "opacity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "bump_scale": ("FLOAT", {"default": 0.03, "min": -1.0, "max": 1.0, "step": 0.001}),
                "viewport_capture": ("STRING", {"default": "", "multiline": False}),
                "viewport_mask_capture": ("STRING", {"default": "", "multiline": False}),
            },
            "optional": {
                "diffuse_texture": ("IMAGE",),
                "bump_map": ("IMAGE",),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("composite", "object_mask")
    FUNCTION = "render"
    CATEGORY = "WepeNerd/3D"
    OUTPUT_NODE = False

    def render(
        self,
        background_image,
        obj_model,
        x_offset,
        y_offset,
        z_offset,
        scale,
        rotate_x,
        rotate_y,
        rotate_z,
        camera_zoom,
        light_yaw,
        light_pitch,
        light_intensity,
        wireframe_overlay,
        opacity,
        bump_scale,
        viewport_capture,
        viewport_mask_capture,
        diffuse_texture=None,
        bump_map=None,
    ):
        np, torch, Image = _import_core_render_dependencies()

        if not isinstance(obj_model, dict) or not obj_model.get("path"):
            raise ValueError("Connect a Load OBJ (WepeNerd) node to obj_model.")
        model_path = _resolve_obj_path(obj_model["path"])
        background = _tensor_image_to_pil(background_image, np, Image)
        width, height = background.size

        captured = _data_url_to_pil(viewport_capture, Image)
        captured_mask = _data_url_to_pil(viewport_mask_capture, Image)
        if captured is not None:
            if captured.size != (width, height):
                captured = captured.resize((width, height), Image.Resampling.LANCZOS)
            composite = np.asarray(captured.convert("RGB"), dtype=np.float32) / 255.0

            if captured_mask is not None:
                if captured_mask.size != (width, height):
                    captured_mask = captured_mask.resize((width, height), Image.Resampling.LANCZOS)
                mask = np.asarray(captured_mask, dtype=np.float32)
                if mask.ndim == 3:
                    mask = mask[..., 3] if mask.shape[-1] == 4 else mask[..., 0]
                mask = np.clip(mask / 255.0, 0.0, 1.0)
            else:
                mask = np.zeros((height, width), dtype=np.float32)

            return (
                torch.from_numpy(np.clip(composite, 0.0, 1.0)).unsqueeze(0),
                torch.from_numpy(mask).unsqueeze(0),
            )

        color = np.asarray(
            _render_clay_obj_rgba(
                model_path,
                width,
                height,
                scale,
                rotate_x,
                rotate_y,
                rotate_z,
                x_offset=x_offset,
                y_offset=y_offset,
                z_offset=z_offset,
                camera_zoom=camera_zoom,
                light_yaw=light_yaw,
                light_pitch=light_pitch,
                light_intensity=light_intensity,
                wireframe_overlay=wireframe_overlay,
            ),
            dtype=np.float32,
        ) / 255.0
        background_np = np.asarray(background, dtype=np.float32) / 255.0
        alpha = color[..., 3:4] * max(0.0, min(1.0, float(opacity)))
        composite = color[..., :3] * alpha + background_np * (1.0 - alpha)
        mask = np.clip(alpha[..., 0], 0.0, 1.0)

        image_tensor = torch.from_numpy(np.clip(composite, 0.0, 1.0)).unsqueeze(0)
        mask_tensor = torch.from_numpy(mask).unsqueeze(0)
        return (image_tensor, mask_tensor)


# ================================================================== #
#  Registration
# ================================================================== #

NODE_CLASS_MAPPINGS = {
    "WN_ResolutionSuggest": WN_ResolutionSuggest,
    "WN_DragResolution":    WN_DragResolution,
    "WN_LoadOBJ": WN_LoadOBJ,
    "WN_3DProductPlacement": WN_3DProductPlacement,
    "WN_LiquifyImage": WN_LiquifyImage,
    "WN_Slider": WN_Slider,
    "WepeNerdLoadLoraMasked": WepeNerdLoadLoraMasked,
    "WN_MaskedLoraSnapshot": WN_MaskedLoraSnapshot,
    "WN_VideoExactFramesFPS": WN_VideoExactFramesFPS,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_ResolutionSuggest": "Resolution Suggest (WepeNerd)",
    "WN_DragResolution":    "Drag Resolution (WepeNerd)",
    "WN_LoadOBJ": "Load OBJ (WepeNerd)",
    "WN_3DProductPlacement": "3D Product Placement (WepeNerd)",
    "WN_LiquifyImage": "Liquify Image (WepeNerd)",
    "WN_Slider": "Slider",
    "WepeNerdLoadLoraMasked": "Load LoRA Masked",
    "WN_MaskedLoraSnapshot": "Masked LoRA Image Snapshot (internal)",
    "WN_VideoExactFramesFPS": "Exact Video Frames/FPS (WepeNerd)",
}

NODE_CLASS_MAPPINGS.update(GGUF_NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(GGUF_NODE_DISPLAY_NAME_MAPPINGS)

_register_3d_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
