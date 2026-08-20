"""Comfy folder-path integration for GGUF models and projectors."""

from __future__ import annotations

from pathlib import Path
import threading
import time


PROJECTOR_TOKENS = ("mmproj", "projector", "vision")
_CACHE_TTL_SECONDS = 5.0
_cache_lock = threading.RLock()
_cache: dict[bool, tuple[float, dict[str, Path]]] = {}
_registered = False


def _folder_paths():
    try:
        import folder_paths
    except ImportError:
        return None
    return folder_paths


def _register_folders():
    global _registered
    folder_paths = _folder_paths()
    if folder_paths is None:
        return None
    if not _registered:
        primary = Path(folder_paths.models_dir) / "LLM"
        primary.mkdir(parents=True, exist_ok=True)
        try:
            folder_paths.add_model_folder_path("LLM", str(primary), is_default=True)
        except TypeError:
            folder_paths.add_model_folder_path("LLM", str(primary))
        legacy = Path(folder_paths.models_dir) / "llm_gguf"
        if legacy.is_dir():
            try:
                folder_paths.add_model_folder_path("llm_gguf", str(legacy), is_default=True)
            except TypeError:
                folder_paths.add_model_folder_path("llm_gguf", str(legacy))
        _registered = True
    return folder_paths


def _is_projector(name: str) -> bool:
    filename = Path(name).name.lower()
    return any(token in filename for token in PROJECTOR_TOKENS)


def _registered_index(projectors: bool) -> dict[str, Path]:
    folder_paths = _register_folders()
    if folder_paths is None:
        return _fallback_index(projectors)

    result: dict[str, Path] = {}
    categories = ["LLM"]
    try:
        folder_paths.get_folder_paths("llm_gguf")
        categories.append("llm_gguf")
    except KeyError:
        pass

    for category in categories:
        try:
            names = folder_paths.get_filename_list(category)
        except KeyError:
            continue
        for name in names:
            if Path(name).suffix.lower() != ".gguf" or _is_projector(name) != projectors:
                continue
            full_path = folder_paths.get_full_path(category, name)
            if full_path:
                result[f"{category}/{Path(name).as_posix()}"] = Path(full_path).resolve()
    return result


def _fallback_index(projectors: bool) -> dict[str, Path]:
    primary = Path.cwd() / "models" / "LLM"
    result: dict[str, Path] = {}
    if not primary.is_dir():
        return result
    root = primary.resolve()
    for path in primary.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".gguf" or _is_projector(path.name) != projectors:
            continue
        resolved = path.resolve()
        if root in resolved.parents:
            result[f"LLM/{path.relative_to(primary).as_posix()}"] = resolved
    return result


def _index(projectors: bool) -> dict[str, Path]:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(projectors)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return dict(cached[1])
    result = dict(sorted(_registered_index(projectors).items(), key=lambda item: item[0].lower()))
    with _cache_lock:
        _cache[projectors] = (now, result)
    return dict(result)


def clear_discovery_cache() -> None:
    with _cache_lock:
        _cache.clear()


def discover_models() -> list[str]:
    return list(_index(projectors=False))


def discover_projectors() -> list[str]:
    return ["(none)", *_index(projectors=True)]


def resolve_choice(choice: str, projector: bool = False) -> str:
    path = _index(projectors=projector).get(choice)
    if path is None:
        clear_discovery_cache()
        path = _index(projectors=projector).get(choice)
    if path is None:
        kind = "projector" if projector else "model"
        raise ValueError(
            f"Unknown GGUF {kind} selection: {choice!r}. Refresh ComfyUI after "
            "adding or moving model files under ComfyUI/models/LLM."
        )
    return str(path)
