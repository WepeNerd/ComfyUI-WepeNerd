import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("web_cache", Path(__file__).resolve().parents[1] / "web_cache.py")
web_cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_cache)


def test_only_this_packages_helper_modules_are_marked():
    assert web_cache.is_package_module("/extensions/ComfyUI-WepeNerd/speedpaint_stroke.mjs", "ComfyUI-WepeNerd")
    assert web_cache.is_package_module("/api/extensions/ComfyUI-WepeNerd/mask_input.mjs", "ComfyUI-WepeNerd")
    assert not web_cache.is_package_module("/extensions/ComfyUI-WepeNerd/wn_speedpaint.js", "ComfyUI-WepeNerd")
    assert not web_cache.is_package_module("/extensions/Other/speedpaint_stroke.mjs", "ComfyUI-WepeNerd")
