"""ComfyUI-WepeNerd: independent WepeNerd node package."""

from .resolution_suggest import WN_ResolutionSuggest
from .drag_resolution import WN_DragResolution
from .slider_node import WN_Slider
from .resize_megapixels_node import WN_ResizeMegapixels
from .speedpaint_node import WN_Speedpaint
from .liquify_node import WN_LiquifyImage
from .masked_lora_node import WepeNerdLoadLoraMasked
from .masked_lora_node import WN_MaskedLoraSnapshot
from .speedpaint_node import register_speedpaint_routes

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {
    'WN_ResolutionSuggest': WN_ResolutionSuggest,
    'WN_DragResolution': WN_DragResolution,
    'WN_Slider': WN_Slider,
    'WN_ResizeMegapixels': WN_ResizeMegapixels,
    'WN_Speedpaint': WN_Speedpaint,
    'WN_LiquifyImage': WN_LiquifyImage,
    'WepeNerdLoadLoraMasked': WepeNerdLoadLoraMasked,
    'WN_MaskedLoraSnapshot': WN_MaskedLoraSnapshot,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_ResolutionSuggest": "Resolution Suggest (WepeNerd)",
    "WN_DragResolution": "Drag Resolution (WepeNerd)",
    "WN_Slider": "Slider",
    "WN_ResizeMegapixels": "Resize Image Megapixels (WepeNerd)",
    "WN_Speedpaint": "Speedpaint",
    "WN_LiquifyImage": "Liquify Image (WepeNerd)",
    "WepeNerdLoadLoraMasked": "Load LoRA Masked",
    "WN_MaskedLoraSnapshot": "Masked LoRA Image Snapshot (internal)"
}

register_speedpaint_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
