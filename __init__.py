"""ComfyUI-WepeNerd: independent WepeNerd node package."""

from .resolution_suggest import WN_ResolutionSuggest
from .drag_resolution import WN_DragResolution
from .slider_node import WN_Slider
from .resize_megapixels_node import WN_ResizeMegapixels
from .load_video_node import WN_LoadVideo
from .speedpaint_node import WN_Speedpaint
from .liquify_node import WN_LiquifyImage
from .paint_mask_node import WN_PaintMask
from .sigma_curve_node import WN_SigmaCurve
from .masked_lora_node import WepeNerdLoadLoraMasked
from .masked_lora_node import WN_MaskedLoraSnapshot
from .speedpaint_node import register_speedpaint_routes
from .web_cache import register_module_cache_headers
from .qwen_edit_align import NODE_CLASS_MAPPINGS as QWEN_ALIGN_NODES
from .qwen_edit_align import NODE_DISPLAY_NAME_MAPPINGS as QWEN_ALIGN_NAMES

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {
    'WN_ResolutionSuggest': WN_ResolutionSuggest,
    'WN_DragResolution': WN_DragResolution,
    'WN_Slider': WN_Slider,
    'WN_ResizeMegapixels': WN_ResizeMegapixels,
    'WN_LoadVideo': WN_LoadVideo,
    'WN_Speedpaint': WN_Speedpaint,
    'WN_LiquifyImage': WN_LiquifyImage,
    'WN_PaintMask': WN_PaintMask,
    'WN_SigmaCurve': WN_SigmaCurve,
    'WepeNerdLoadLoraMasked': WepeNerdLoadLoraMasked,
    'WN_MaskedLoraSnapshot': WN_MaskedLoraSnapshot,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_ResolutionSuggest": "Resolution Suggest (WepeNerd)",
    "WN_DragResolution": "Drag Resolution (WepeNerd)",
    "WN_Slider": "Slider",
    "WN_ResizeMegapixels": "Resize Image Megapixels (WepeNerd)",
    "WN_LoadVideo": "Load Video (Upload) (WepeNerd)",
    "WN_Speedpaint": "Speedpaint",
    "WN_LiquifyImage": "Liquify Image (WepeNerd)",
    "WN_PaintMask": "Paint Mask (WepeNerd)",
    "WN_SigmaCurve": "Sigma Curve (WepeNerd)",
    "WepeNerdLoadLoraMasked": "Load LoRA Masked",
    "WN_MaskedLoraSnapshot": "Masked LoRA Image Snapshot (internal)"
}

NODE_CLASS_MAPPINGS.update(QWEN_ALIGN_NODES)
NODE_DISPLAY_NAME_MAPPINGS.update(QWEN_ALIGN_NAMES)

register_speedpaint_routes()
register_module_cache_headers()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
