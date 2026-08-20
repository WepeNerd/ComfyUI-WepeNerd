"""Load bundled Local AI prompt-enhancement skills safely."""

from __future__ import annotations

from pathlib import Path
import threading


BUILTIN_SKILLS = {
    "h3": "H3_Skill.md",
    "krea2": "Krea2_Skill.md",
}

_SKILLS_DIR = (Path(__file__).resolve().parent / "skills").resolve()
_cache_lock = threading.RLock()
_cache: dict[str, tuple[int, int, str]] = {}


def load_skill(name: str) -> str:
    """Return a bundled skill, refreshing the cache when its file changes."""
    key = str(name).strip().lower().replace(" ", "")
    try:
        filename = BUILTIN_SKILLS[key]
    except KeyError as exc:
        available = ", ".join(sorted(BUILTIN_SKILLS))
        raise ValueError(f"Unknown Local AI skill {name!r}. Available skills: {available}.") from exc

    path = (_SKILLS_DIR / filename).resolve()
    if path.parent != _SKILLS_DIR:
        raise ValueError("Bundled skill path escaped the local skills directory.")
    try:
        stat = path.stat()
    except OSError as exc:
        raise FileNotFoundError(
            f"Bundled Local AI skill is missing: {path}. Reinstall ComfyUI-WepeNerd."
        ) from exc

    signature = (stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[:2] == signature:
            return cached[2]

    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Could not read bundled Local AI skill {path}: {exc}") from exc
    if not text:
        raise ValueError(f"Bundled Local AI skill is empty: {path}. Reinstall ComfyUI-WepeNerd.")

    with _cache_lock:
        _cache[key] = (*signature, text)
    return text


def clear_skill_cache() -> None:
    with _cache_lock:
        _cache.clear()
