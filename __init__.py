"""ComfyUI-WepeNerd: independent WepeNerd node package."""

from .resolution_suggest import WN_ResolutionSuggest
from .drag_resolution import WN_DragResolution
from .slider_node import WN_Slider
from .resize_megapixels_node import WN_ResizeMegapixels
from .zoom_crop_node import WN_ZoomCrop
from .load_video_node import WN_LoadVideo, register_load_video_routes
from .save_video_node import WN_SaveVideo
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
from .qwen_edit_mask_node import NODE_CLASS_MAPPINGS as QWEN_MASK_NODES
from .qwen_edit_mask_node import NODE_DISPLAY_NAME_MAPPINGS as QWEN_MASK_NAMES
from .frame_number_node import NODE_CLASS_MAPPINGS as FRAME_NUMBER_NODES
from .frame_number_node import NODE_DISPLAY_NAME_MAPPINGS as FRAME_NUMBER_NAMES
from .frame_number_node import register_frame_number_routes
from .frame_slicer_node import NODE_CLASS_MAPPINGS as FRAME_SLICER_NODES
from .frame_slicer_node import NODE_DISPLAY_NAME_MAPPINGS as FRAME_SLICER_NAMES
from .ripple_node import NODE_CLASS_MAPPINGS as RIPPLE_NODES
from .ripple_node import NODE_DISPLAY_NAME_MAPPINGS as RIPPLE_NAMES
from .ripple_node import register_ripple_routes
from .vae_degrid_node import NODE_CLASS_MAPPINGS as VAE_DEGRID_NODES
from .vae_degrid_node import NODE_DISPLAY_NAME_MAPPINGS as VAE_DEGRID_NAMES

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {
    'WN_ResolutionSuggest': WN_ResolutionSuggest,
    'WN_DragResolution': WN_DragResolution,
    'WN_Slider': WN_Slider,
    'WN_ResizeMegapixels': WN_ResizeMegapixels,
    'WN_ZoomCrop': WN_ZoomCrop,
    'WN_LoadVideo': WN_LoadVideo,
    'WN_SaveVideo': WN_SaveVideo,
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
    "WN_ZoomCrop": "Zoom Crop (WepeNerd)",
    "WN_LoadVideo": "Load Video (Upload) (WepeNerd)",
    "WN_SaveVideo": "Save Video (WepeNerd)",
    "WN_Speedpaint": "Speedpaint",
    "WN_LiquifyImage": "Liquify Image (WepeNerd)",
    "WN_PaintMask": "Paint Mask (WepeNerd)",
    "WN_SigmaCurve": "Sigma Curve (WepeNerd)",
    "WepeNerdLoadLoraMasked": "Load LoRA Masked",
    "WN_MaskedLoraSnapshot": "Masked LoRA Image Snapshot (internal)"
}

NODE_CLASS_MAPPINGS.update(QWEN_ALIGN_NODES)
NODE_DISPLAY_NAME_MAPPINGS.update(QWEN_ALIGN_NAMES)
NODE_CLASS_MAPPINGS.update(QWEN_MASK_NODES)
NODE_DISPLAY_NAME_MAPPINGS.update(QWEN_MASK_NAMES)
NODE_CLASS_MAPPINGS.update(FRAME_NUMBER_NODES)
NODE_DISPLAY_NAME_MAPPINGS.update(FRAME_NUMBER_NAMES)
NODE_CLASS_MAPPINGS.update(FRAME_SLICER_NODES)
NODE_DISPLAY_NAME_MAPPINGS.update(FRAME_SLICER_NAMES)
NODE_CLASS_MAPPINGS.update(RIPPLE_NODES)
NODE_DISPLAY_NAME_MAPPINGS.update(RIPPLE_NAMES)
NODE_CLASS_MAPPINGS.update(VAE_DEGRID_NODES)
NODE_DISPLAY_NAME_MAPPINGS.update(VAE_DEGRID_NAMES)

register_speedpaint_routes()
register_frame_number_routes()
register_load_video_routes()
register_ripple_routes()
register_module_cache_headers()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
