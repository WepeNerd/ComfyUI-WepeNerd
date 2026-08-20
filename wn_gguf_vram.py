"""Comfy-aware VRAM handoff before starting an external CUDA process."""

from __future__ import annotations

import logging
from pathlib import Path


log = logging.getLogger("ComfyUI-WepeNerd.GGUF")


def _mb(value) -> float:
    if isinstance(value, (tuple, list)):
        value = value[0]
    return float(value) / (1024.0 * 1024.0)


def free_vram_for_external(
    target_free_vram_mb: int,
    aggressive: bool = False,
    handoff_mode: str = "auto",
    model_path: str | None = None,
) -> dict:
    import comfy.model_management as model_management

    if handoff_mode not in ("auto", "always", "never"):
        raise ValueError(f"Unknown Comfy VRAM handoff mode: {handoff_mode}")
    device = model_management.get_torch_device()
    before = model_management.get_free_memory(device)
    if handoff_mode == "never":
        log.info("GGUF VRAM handoff skipped by configuration")
        after = before
    elif aggressive:
        log.info("Aggressive GGUF handoff: unloading all Comfy-managed models")
        model_management.unload_all_models()
        model_management.soft_empty_cache()
        after = model_management.get_free_memory(device)
    elif target_free_vram_mb > 0:
        model_management.free_memory(int(target_free_vram_mb) * 1024 * 1024, device)
        model_management.soft_empty_cache()
        after = model_management.get_free_memory(device)
    else:
        model_management.soft_empty_cache()
        after = model_management.get_free_memory(device)
    info = {
        "device": str(device),
        "before_free_mb": _mb(before),
        "after_free_mb": _mb(after),
        "target_free_mb": int(target_free_vram_mb),
        "aggressive": bool(aggressive),
        "handoff_mode": handoff_mode,
    }
    if model_path:
        try:
            info["model_file_mb"] = Path(model_path).stat().st_size / (1024.0 * 1024.0)
        except OSError:
            pass
    log.info(
        "GGUF VRAM handoff device=%s before=%.0fMB after=%.0fMB target=%dMB aggressive=%s",
        info["device"],
        info["before_free_mb"],
        info["after_free_mb"],
        info["target_free_mb"],
        info["aggressive"],
    )
    if handoff_mode != "never" and target_free_vram_mb > 0 and info["after_free_mb"] < target_free_vram_mb:
        log.warning(
            "GGUF requested %dMB free VRAM but Comfy could make %.0fMB available; "
            "llama.cpp will make the final fit/offload decision",
            target_free_vram_mb,
            info["after_free_mb"],
        )
    return info
