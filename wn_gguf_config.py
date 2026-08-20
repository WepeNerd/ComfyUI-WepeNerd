"""Configuration for the local llama.cpp GGUF backend."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


def _server_candidates(value: str):
    value = (value or "").strip()
    if value and value.lower() != "auto":
        yield Path(value)
        found = shutil.which(value)
        if found:
            yield Path(found)
        return

    env_path = os.environ.get("LLAMA_SERVER_PATH", "").strip()
    if env_path:
        yield Path(env_path)

    names = ("llama-server.exe", "llama-server") if os.name == "nt" else ("llama-server",)
    for name in names:
        found = shutil.which(name)
        if found:
            yield Path(found)

    here = Path(__file__).resolve().parent
    directories = [here / "bin", here.parent.parent / "models" / "LLM"]
    if os.name == "nt":
        directories.append(Path("C:/llamacpp"))
    for directory in directories:
        for name in names:
            yield directory / name


@dataclass(frozen=True)
class WNGGUFConfig:
    model_path: str
    mmproj_path: str | None = None
    server_executable: str = "auto"
    context_size: int = 8192
    gpu_layers: int = -1
    target_free_vram_mb: int = 24576
    aggressive_vram_handoff: bool = False
    host: str = "127.0.0.1"
    port: int = 0
    release_after_generate: bool = True
    startup_timeout_s: float = 300.0
    request_timeout_s: float = 600.0
    extra_args: tuple[str, ...] = ()
    keep_alive_seconds: int = 0
    flash_attn: str = "auto"
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"
    image_min_tokens: int = 0
    image_max_tokens: int = 0
    cuda_visible_devices: str = ""
    comfy_vram_handoff: str = "auto"
    native_video_max_mb: int = 96

    def resolved_executable(self) -> str:
        for candidate in _server_candidates(self.server_executable):
            try:
                if candidate.is_file():
                    return str(candidate.resolve())
            except OSError:
                continue
        raise FileNotFoundError(
            "llama-server was not found. Install a recent llama.cpp server build, "
            "then either put llama-server.exe on PATH, set LLAMA_SERVER_PATH, "
            "place it in C:\\llamacpp or ComfyUI-WepeNerd/bin, or enter its full "
            "path in Local AI Model (Advanced). The .gguf model is not itself an executable."
        )

    def validate(self) -> None:
        model = Path(self.model_path)
        if not model.is_file():
            raise FileNotFoundError(f"GGUF model not found: {model}")
        if model.suffix.lower() != ".gguf":
            raise ValueError(f"Model must be a .gguf file: {model}")

        if self.mmproj_path:
            projector = Path(self.mmproj_path)
            if not projector.is_file():
                raise FileNotFoundError(f"GGUF projector not found: {projector}")
            if projector.resolve() == model.resolve():
                raise ValueError("The model and projector must be different files.")

        self.resolved_executable()
        if self.host not in ("127.0.0.1", "localhost"):
            raise ValueError("The local GGUF backend may only bind to localhost.")
        if self.context_size < 256:
            raise ValueError("context_size must be at least 256")
        if self.target_free_vram_mb < 0:
            raise ValueError("target_free_vram_mb cannot be negative")
        if self.port < 0 or self.port > 65535:
            raise ValueError("port must be 0 (automatic) or between 1 and 65535")
        if self.startup_timeout_s <= 0 or self.request_timeout_s <= 0:
            raise ValueError("Server timeouts must be positive")
        if self.keep_alive_seconds < 0:
            raise ValueError("keep_alive_seconds cannot be negative")
        if self.flash_attn not in ("auto", "on", "off"):
            raise ValueError("flash_attn must be auto, on, or off")
        if self.cache_type_k not in ("f16", "q8_0") or self.cache_type_v not in ("f16", "q8_0"):
            raise ValueError("KV cache types must be f16 or q8_0")
        if self.image_min_tokens < 0 or self.image_max_tokens < 0:
            raise ValueError("Image token controls cannot be negative")
        if self.comfy_vram_handoff not in ("auto", "always", "never"):
            raise ValueError("comfy_vram_handoff must be auto, always, or never")
        if self.native_video_max_mb < 1:
            raise ValueError("native_video_max_mb must be positive")

    def server_key(self) -> tuple:
        """Fields that determine whether a running server can be reused."""
        return (
            str(Path(self.model_path).resolve()),
            str(Path(self.mmproj_path).resolve()) if self.mmproj_path else None,
            self.resolved_executable(),
            self.context_size,
            self.gpu_layers,
            self.host,
            self.port,
            self.flash_attn,
            self.cache_type_k,
            self.cache_type_v,
            self.image_min_tokens,
            self.image_max_tokens,
            self.cuda_visible_devices.strip(),
            self.extra_args,
        )
