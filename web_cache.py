"""Keep the browser from reusing stale copies of this package's helper modules.

ComfyUI marks ``.js`` files ``no-store`` but sends no cache header for ``.mjs``.
Browsers then cache those modules heuristically, so an updated ``wn_*.js``
entry point can run against an old helper module (for example a Speedpaint
stroke module from before the eraser existed, which paints the brush colour).
"""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.name


def is_package_module(path, package=PACKAGE):
    return path.endswith(".mjs") and f"/extensions/{package}/" in path


def register_module_cache_headers():
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    instance = PromptServer.instance
    if instance is None or getattr(instance, "_wn_module_cache_registered", False):
        return

    @web.middleware
    async def no_store_modules(request, handler):
        response = await handler(request)
        if is_package_module(request.path):
            response.headers["Cache-Control"] = "no-store"
        return response

    try:
        instance.app.middlewares.append(no_store_modules)
    except RuntimeError:  # application already started and frozen
        return
    instance._wn_module_cache_registered = True
