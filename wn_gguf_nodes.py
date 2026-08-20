"""ComfyUI nodes for local GGUF text, image, and video generation."""

from __future__ import annotations

import logging
import os
import re
import shlex
import time

from .wn_gguf_config import WNGGUFConfig
from .wn_gguf_image import comfy_image_to_data_url, encode_image_batch
from .wn_gguf_models import discover_models, discover_projectors, resolve_choice
from .wn_gguf_payloads import (
    REASONING_EFFORTS,
    build_chat_payload,
    native_video_content,
    sampled_video_content,
)
from .wn_gguf_server import RequestRejectedError, SERVER_MANAGER
from .wn_gguf_skills import load_skill
from .wn_gguf_video import prepare_native_video, prepare_sampled_frames, video_metadata
from .wn_gguf_vram import free_vram_for_external


log = logging.getLogger("ComfyUI-WepeNerd.LocalAI")

PROMPT_STYLES = {
    "generic": "Rewrite the user's prompt into one polished generation prompt. Preserve intent, add only useful concrete detail, and return only the prompt.",
    "flux": "Rewrite as a clear natural-language FLUX image prompt. Prioritize subject, composition, materials, lighting, and style. Return only the prompt.",
    "ltx_video": "Rewrite as a concise LTX video prompt describing the shot, subject action, camera motion, setting, lighting, and temporal progression. Return only the prompt.",
    "minimax_h3": "bundled:H3",
    "krea2": "bundled:Krea2",
    "wan": "Rewrite as a focused Wan video prompt. State subject, action over time, environment, camera movement, composition, and lighting without unnecessary prose. Return only the prompt.",
    "sdxl": "Rewrite as a concise SDXL image prompt using concrete visual concepts, composition, lighting, lens or viewpoint, materials, and style. Return only the prompt.",
}

IMAGE_CAPTION_STYLES = {
    "dataset_natural": "Write one accurate natural-language dataset caption. Describe visible subjects, actions, setting, composition, viewpoint, lighting, and notable details. Do not invent facts. Return only the caption.",
    "detailed_visual": "Describe the image in precise visual detail, including subjects, spatial relationships, composition, viewpoint, lighting, color, texture, and materials. Do not infer unseen facts. Return only the description.",
    "short": "Write a short factual caption naming the main visible subject, action, and setting. Return only the caption.",
    "booru_tags": "Return a concise comma-separated list of accurate booru-style visual tags. Do not add prose or unsupported tags.",
    "motion_camera": "Describe visible action or motion cues and the camera viewpoint, framing, and composition. Do not invent temporal events that a still image cannot establish. Return only the caption.",
}

VIDEO_CAPTION_STYLES = {
    "dataset_natural": "Write one accurate natural-language video dataset caption. Describe what is visible, what changes, subject and object motion, camera motion, framing, environment, and clear beginning-to-end progression. Do not invent audio or dialogue. Return only the caption.",
    "detailed_visual": "Describe this video's visible temporal progression in detail: subjects, actions, object motion, camera motion, shot composition, environment, and visually clear lighting or weather changes. Do not invent audio or dialogue. Return only the description.",
    "short": "Write a short factual video caption covering the main subject, action over time, setting, and camera movement. Do not mention unheard audio. Return only the caption.",
    "motion_camera": "Focus on temporal action, subject and object motion, camera movement, shot/framing changes, and how the scene develops from beginning to end. Do not invent audio or dialogue. Return only the caption.",
}


def _model_choices():
    models = discover_models()
    return models or ["<put .gguf models in ComfyUI/models/LLM>"]


def _clean_projector_choices():
    return ["Auto / None", *[item for item in discover_projectors() if item != "(none)"]]


def _split_extra_args(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    values = shlex.split(value, posix=os.name != "nt")
    if os.name == "nt":
        values = [v[1:-1] if len(v) > 1 and v[0] == v[-1] == '"' else v for v in values]
    return tuple(values)


def _validate_request(config: WNGGUFConfig, prompt: str, max_tokens: int) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    if int(max_tokens) <= 0:
        raise ValueError("max_tokens must be positive")
    if int(max_tokens) >= int(config.context_size):
        raise ValueError("max_tokens must be smaller than context_size to leave room for the prompt and media")
    if int(max_tokens) > int(config.context_size) - 256:
        log.warning("GGUF max_tokens leaves very little context room for prompt or media tokens")
    config.validate()


def _sampler_values(preset, temperature, top_p, top_k, min_p, reasoning_effort):
    if preset == "qwen_non_thinking":
        return 0.7, 0.8, 20, 0.0, "none" if reasoning_effort == "default" else reasoning_effort
    if preset == "qwen_thinking":
        return 0.6, 0.95, 20, 0.0, "high" if reasoning_effort == "default" else reasoning_effort
    return temperature, top_p, top_k, min_p, reasoning_effort


def _make_payload(
    prompt,
    system_prompt,
    max_tokens,
    temperature,
    top_p,
    top_k,
    min_p,
    repetition_penalty,
    presence_penalty,
    frequency_penalty,
    seed,
    reasoning_effort,
    image_data_url=None,
    user_content=None,
):
    return build_chat_payload(
        prompt=prompt,
        system_prompt=system_prompt,
        image_data_url=image_data_url,
        user_content=user_content,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        min_p=min_p,
        repetition_penalty=repetition_penalty,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
        seed=seed,
        reasoning_effort=reasoning_effort,
    )


def _acquire_prepared(config: WNGGUFConfig):
    if not SERVER_MANAGER.is_compatible(config):
        SERVER_MANAGER.stop()
        free_vram_for_external(
            target_free_vram_mb=config.target_free_vram_mb,
            aggressive=config.aggressive_vram_handoff,
            handoff_mode=config.comfy_vram_handoff,
            model_path=config.model_path,
        )
    return SERVER_MANAGER.acquire(config)


def _finish_request(config: WNGGUFConfig, error: BaseException | None = None) -> None:
    if config.release_after_generate or (error is not None and SERVER_MANAGER.is_fatal_error(error)):
        SERVER_MANAGER.stop()
    else:
        SERVER_MANAGER.request_finished(config)


def _run_payloads(config: WNGGUFConfig, payloads: list[dict], require_image: bool = False) -> list[str]:
    error = None
    try:
        handle = _acquire_prepared(config)
        capability = _handle_capability(handle, "image") if require_image else None
        if require_image and capability is False:
            raise RequestRejectedError(
                "The loaded llama-server model does not report image/vision support. "
                "Check that the selected projector matches this model."
            )
        if require_image and capability is None:
            log.warning("Image capability is missing from /props; attempting the request")
        progress = _progress_bar(len(payloads)) if len(payloads) > 1 else None
        results = []
        for payload in payloads:
            results.append(SERVER_MANAGER.chat_completion(handle, payload, config.request_timeout_s))
            if progress is not None:
                progress.update(1)
        return results
    except BaseException as exc:
        error = exc
        raise
    finally:
        _finish_request(config, error)


def _style_prompt(style: str, styles: dict[str, str], override: str) -> str:
    if override.strip():
        return override.strip()
    if style == "custom":
        raise ValueError("custom style requires system_prompt_override")
    if style == "minimax_h3":
        return load_skill("h3")
    if style == "krea2":
        return load_skill("krea2")
    try:
        return styles[style]
    except KeyError as exc:
        raise ValueError(f"Unknown caption/prompt style: {style}") from exc


def _clean_caption(text: str, style: str, prefix: str, banned_phrases: str) -> str:
    value = text.strip()
    if style != "custom":
        value = re.sub(
            r"^(?:sure[,.!:\s-]*|here(?:'s| is) (?:the|a) (?:caption|description)[^:]*:\s*)",
            "",
            value,
            flags=re.IGNORECASE,
        )
    phrases = [item.strip() for item in banned_phrases.splitlines() if item.strip()]
    for phrase in phrases:
        value = re.sub(re.escape(phrase), "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,;:])\s*([,;:])", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip(" ,")
    if prefix.strip():
        separator = ", " if style == "booru_tags" else " "
        value = f"{prefix.strip()}{separator}{value}".strip(" ,")
    return value


def _progress_bar(total: int):
    try:
        from comfy.utils import ProgressBar
    except ImportError:
        return None
    return ProgressBar(total)


def _handle_capability(handle, modality: str) -> bool | None:
    capability = getattr(handle, "capability", None)
    if callable(capability):
        return capability(modality)
    supports = getattr(handle, "supports", None)
    return bool(supports(modality)) if callable(supports) else None


class WN_GGUFLLMConfig:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (_model_choices(),),
                "llama_server": ("STRING", {"default": "auto"}),
                "context_size": ("INT", {"default": 8192, "min": 256, "max": 262144, "step": 256}),
                "gpu_layers": ("INT", {"default": -1, "min": -1, "max": 999}),
                "target_free_vram_mb": ("INT", {"default": 24576, "min": 0, "max": 262144, "step": 256}),
                "aggressive_vram_handoff": ("BOOLEAN", {"default": False}),
                "release_after_generate": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "mmproj": (discover_projectors(),),
                "startup_timeout_s": ("FLOAT", {"default": 300.0, "min": 5.0, "max": 1800.0, "step": 5.0}),
                "request_timeout_s": ("FLOAT", {"default": 600.0, "min": 5.0, "max": 7200.0, "step": 5.0}),
                "extra_server_args": ("STRING", {"default": ""}),
                "keep_alive_seconds": ("INT", {"default": 0, "min": 0, "max": 86400}),
                "flash_attn": (["auto", "on", "off"],),
                "cache_type_k": (["f16", "q8_0"],),
                "cache_type_v": (["f16", "q8_0"],),
                "image_min_tokens": ("INT", {"default": 0, "min": 0, "max": 65536}),
                "image_max_tokens": ("INT", {"default": 0, "min": 0, "max": 65536}),
                "cuda_visible_devices": ("STRING", {"default": ""}),
                "comfy_vram_handoff": (["auto", "always", "never"],),
                "native_video_max_mb": ("INT", {"default": 96, "min": 1, "max": 1024}),
            },
        }

    RETURN_TYPES = ("GGUF_LLM_CONFIG",)
    RETURN_NAMES = ("config",)
    FUNCTION = "build"
    CATEGORY = "WepeNerd/Local AI/Advanced"

    def build(
        self,
        model,
        llama_server,
        context_size,
        gpu_layers,
        target_free_vram_mb,
        aggressive_vram_handoff,
        release_after_generate,
        mmproj="(none)",
        startup_timeout_s=300.0,
        request_timeout_s=600.0,
        extra_server_args="",
        keep_alive_seconds=0,
        flash_attn="auto",
        cache_type_k="f16",
        cache_type_v="f16",
        image_min_tokens=0,
        image_max_tokens=0,
        cuda_visible_devices="",
        comfy_vram_handoff="auto",
        native_video_max_mb=96,
    ):
        if model.startswith("<"):
            raise RuntimeError("No GGUF models found in ComfyUI/models/LLM")
        config = WNGGUFConfig(
            model_path=resolve_choice(model),
            mmproj_path=None if mmproj in ("", "(none)") else resolve_choice(mmproj, projector=True),
            server_executable=llama_server,
            context_size=int(context_size),
            gpu_layers=int(gpu_layers),
            target_free_vram_mb=int(target_free_vram_mb),
            aggressive_vram_handoff=bool(aggressive_vram_handoff),
            release_after_generate=bool(release_after_generate),
            startup_timeout_s=float(startup_timeout_s),
            request_timeout_s=float(request_timeout_s),
            extra_args=_split_extra_args(extra_server_args),
            keep_alive_seconds=int(keep_alive_seconds),
            flash_attn=flash_attn,
            cache_type_k=cache_type_k,
            cache_type_v=cache_type_v,
            image_min_tokens=int(image_min_tokens),
            image_max_tokens=int(image_max_tokens),
            cuda_visible_devices=cuda_visible_devices,
            comfy_vram_handoff=comfy_vram_handoff,
            native_video_max_mb=int(native_video_max_mb),
        )
        config.validate()
        return (config,)


class WN_GGUFLLMGenerate:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "config": ("GGUF_LLM_CONFIG",),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "system_prompt": ("STRING", {"multiline": True, "default": ""}),
                "max_tokens": ("INT", {"default": 512, "min": 1, "max": 32768}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
                "top_p": ("FLOAT", {"default": 0.95, "min": 0.0, "max": 1.0, "step": 0.01}),
                "top_k": ("INT", {"default": 40, "min": 0, "max": 1000}),
                "repetition_penalty": ("FLOAT", {"default": 1.05, "min": 0.0, "max": 5.0, "step": 0.01}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True}),
                "reasoning_effort": (list(REASONING_EFFORTS),),
                "sampling_preset": (["custom", "qwen_non_thinking", "qwen_thinking"],),
                "min_p": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "presence_penalty": ("FLOAT", {"default": 0.0, "min": -2.0, "max": 2.0, "step": 0.01}),
                "frequency_penalty": ("FLOAT", {"default": 0.0, "min": -2.0, "max": 2.0, "step": 0.01}),
                "image_max_edge": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64}),
                "jpeg_quality": ("INT", {"default": 90, "min": 1, "max": 100}),
            },
            "optional": {"image": ("IMAGE",)},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("generated_text",)
    FUNCTION = "generate"
    CATEGORY = "WepeNerd/Local AI/Advanced"

    def generate(
        self, config, prompt, system_prompt, max_tokens, temperature, top_p, top_k,
        repetition_penalty, seed, reasoning_effort="default", sampling_preset="custom",
        min_p=0.0, presence_penalty=0.0, frequency_penalty=0.0,
        image_max_edge=1024, jpeg_quality=90, image=None,
    ):
        _validate_request(config, prompt, max_tokens)
        image_url = None
        if image is not None:
            if not config.mmproj_path:
                raise ValueError("IMAGE input requires an explicitly selected compatible mmproj")
            image_url = comfy_image_to_data_url(image, "JPEG", image_max_edge, jpeg_quality)
        temperature, top_p, top_k, min_p, reasoning_effort = _sampler_values(
            sampling_preset, temperature, top_p, top_k, min_p, reasoning_effort
        )
        payload = _make_payload(
            prompt, system_prompt, max_tokens, temperature, top_p, top_k, min_p,
            repetition_penalty, presence_penalty, frequency_penalty, seed,
            reasoning_effort, image_data_url=image_url,
        )
        return (_run_payloads(config, [payload], require_image=image_url is not None)[0],)


class WN_GGUFPromptEnhance:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "config": ("GGUF_LLM_CONFIG",),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "max_tokens": ("INT", {"default": 512, "min": 1, "max": 4096}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True}),
                "prompt_style": (list(PROMPT_STYLES) + ["custom"],),
                "reasoning_effort": (list(REASONING_EFFORTS), {"default": "none"}),
            },
            "optional": {"system_prompt_override": ("STRING", {"multiline": True, "default": ""})},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("enhanced_prompt",)
    FUNCTION = "enhance"
    CATEGORY = "WepeNerd/Local AI/Advanced"

    def enhance(self, config, prompt, max_tokens, temperature, seed, prompt_style="generic", reasoning_effort="none", system_prompt_override=""):
        _validate_request(config, prompt, max_tokens)
        system_prompt = _style_prompt(prompt_style, PROMPT_STYLES, system_prompt_override)
        payload = _make_payload(
            prompt, system_prompt, max_tokens, temperature, 0.8, 20, 0.0,
            1.05, 0.0, 0.0, seed, reasoning_effort,
        )
        return (_run_payloads(config, [payload])[0],)


class WN_GGUFCaptionImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "config": ("GGUF_LLM_CONFIG",),
                "image": ("IMAGE",),
                "instruction": ("STRING", {"multiline": True, "default": "Describe this image accurately and in detail."}),
                "max_tokens": ("INT", {"default": 512, "min": 1, "max": 4096}),
                "temperature": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 2.0, "step": 0.01}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True}),
                "caption_style": (list(IMAGE_CAPTION_STYLES) + ["custom"],),
                "reasoning_effort": (list(REASONING_EFFORTS), {"default": "none"}),
            },
            "optional": {
                "system_prompt_override": ("STRING", {"multiline": True, "default": ""}),
                "caption_prefix": ("STRING", {"default": ""}),
                "banned_phrases": ("STRING", {"multiline": True, "default": ""}),
                "image_max_edge": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64}),
                "jpeg_quality": ("INT", {"default": 90, "min": 1, "max": 100}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("caption",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "caption"
    CATEGORY = "WepeNerd/Local AI/Advanced"

    def caption(
        self, config, image, instruction, max_tokens, temperature, seed,
        caption_style="dataset_natural", reasoning_effort="none",
        system_prompt_override="", caption_prefix="", banned_phrases="",
        image_max_edge=1024, jpeg_quality=90,
    ):
        _validate_request(config, instruction, max_tokens)
        if not config.mmproj_path:
            raise ValueError("Image captioning requires an explicitly selected compatible mmproj")
        system_prompt = _style_prompt(caption_style, IMAGE_CAPTION_STYLES, system_prompt_override)
        urls = encode_image_batch(image, image_max_edge, "JPEG", jpeg_quality)
        payloads = [
            _make_payload(
                instruction, system_prompt, max_tokens, temperature, 0.8, 20, 0.0,
                1.05, 0.0, 0.0, seed, reasoning_effort, image_data_url=url,
            )
            for url in urls
        ]
        captions = _run_payloads(config, payloads, require_image=True)
        return ([_clean_caption(value, caption_style, caption_prefix, banned_phrases) for value in captions],)


def _video_payload(
    instruction, system_prompt, max_tokens, temperature, seed, reasoning_effort,
    media_content,
):
    return _make_payload(
        instruction, system_prompt, max_tokens, temperature, 0.8, 20, 0.0,
        1.05, 0.0, 0.0, seed, reasoning_effort, user_content=media_content,
    )


def _caption_video_request(
    config, video, instruction, caption_style, video_mode, sampling_mode,
    sample_frames, sample_fps, max_frames, max_tokens, temperature, seed,
    system_prompt_override="", caption_prefix="", banned_phrases="",
    image_max_edge=1024, jpeg_quality=90, reasoning_effort="none",
):
    _validate_request(config, instruction, max_tokens)
    if not config.mmproj_path:
        raise ValueError("Video captioning requires an explicitly selected compatible projector")
    if video_mode not in ("auto", "native_video", "sampled_frames"):
        raise ValueError(f"Unknown video mode: {video_mode}")
    system_prompt = _style_prompt(caption_style, VIDEO_CAPTION_STYLES, system_prompt_override)

    def run_sampled(handle):
        image_capability = _handle_capability(handle, "image")
        if image_capability is False:
            raise RequestRejectedError(
                "The loaded model/server explicitly reports no image support for sampled video frames"
            )
        if image_capability is None:
            log.warning("Image capability is missing from /props; attempting sampled video frames")
        sampled_media = prepare_sampled_frames(
            video, sampling_mode, sample_frames, sample_fps, max_frames,
            image_max_edge, jpeg_quality,
        )
        payload = _video_payload(
            instruction, system_prompt, max_tokens, temperature, seed, reasoning_effort,
            sampled_video_content(instruction, sampled_media["urls"], sampled_media["timestamps"]),
        )
        result = SERVER_MANAGER.chat_completion(handle, payload, config.request_timeout_s)
        return result, sampled_media

    def run_native(handle):
        native_media = prepare_native_video(video, config.native_video_max_mb)
        payload = _video_payload(
            instruction, system_prompt, max_tokens, temperature, seed, reasoning_effort,
            native_video_content(instruction, native_media["base64"]),
        )
        result = SERVER_MANAGER.chat_completion(handle, payload, config.request_timeout_s)
        return result, native_media

    error = None
    used_mode = None
    fallback_reason = None
    metadata = None
    try:
        handle = _acquire_prepared(config)
        video_capability = _handle_capability(handle, "video")
        if video_mode == "native_video":
            if video_capability is False:
                raise RequestRejectedError("The loaded model/server explicitly reports no native video support")
            if video_capability is None:
                log.warning("Native-video capability is missing from /props; attempting forced native mode")
            caption, metadata = run_native(handle)
            used_mode = "native_video"
        elif video_mode == "sampled_frames":
            caption, metadata = run_sampled(handle)
            used_mode = "sampled_frames"
        elif video_capability is True:
            try:
                caption, metadata = run_native(handle)
                used_mode = "native_video"
            except (RequestRejectedError, OSError, RuntimeError, TypeError, ValueError) as exc:
                fallback_reason = str(exc)
                caption, metadata = run_sampled(handle)
                used_mode = "sampled_frames"
        else:
            if video_capability is None:
                fallback_reason = "native video capability is unknown"
                log.warning("Native-video capability is missing from /props; using sampled frames")
            else:
                fallback_reason = "native video explicitly unsupported"
            caption, metadata = run_sampled(handle)
            used_mode = "sampled_frames"
    except BaseException as exc:
        error = exc
        raise
    finally:
        _finish_request(config, error)

    caption = _clean_caption(caption, caption_style, caption_prefix, banned_phrases)
    metadata = metadata or video_metadata(video)
    timestamps = metadata.get("timestamps", [])
    info_parts = [
        f"mode={used_mode}",
        f"fps={metadata.get('fps')}",
        f"duration={metadata.get('duration')}",
        f"frame_count={metadata.get('frame_count')}",
        f"sampled_frames={len(timestamps)}",
    ]
    if metadata.get("extraction"):
        info_parts.append(f"extraction={metadata['extraction']}")
    if timestamps:
        info_parts.append("timestamps=" + ", ".join(f"{value:.2f}s" for value in timestamps))
    if fallback_reason:
        info_parts.append("native_fallback=" + fallback_reason)
    return caption, "; ".join(info_parts)


class WN_GGUFCaptionVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "config": ("GGUF_LLM_CONFIG",),
                "video": ("VIDEO",),
                "instruction": ("STRING", {"multiline": True, "default": "Describe this video accurately, including subjects, actions, camera motion, setting, and meaningful changes over time."}),
                "caption_style": (list(VIDEO_CAPTION_STYLES) + ["custom"],),
                "video_mode": (["auto", "native_video", "sampled_frames"],),
                "sampling_mode": (["uniform", "fixed_fps"],),
                "sample_frames": ("INT", {"default": 12, "min": 2, "max": 96}),
                "sample_fps": ("FLOAT", {"default": 2.0, "min": 0.01, "max": 120.0, "step": 0.01}),
                "max_frames": ("INT", {"default": 24, "min": 2, "max": 96}),
                "max_tokens": ("INT", {"default": 512, "min": 1, "max": 4096}),
                "temperature": ("FLOAT", {"default": 0.2, "min": 0.0, "max": 2.0, "step": 0.01}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True}),
            },
            "optional": {
                "system_prompt_override": ("STRING", {"multiline": True, "default": ""}),
                "caption_prefix": ("STRING", {"default": ""}),
                "banned_phrases": ("STRING", {"multiline": True, "default": ""}),
                "image_max_edge": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64}),
                "jpeg_quality": ("INT", {"default": 90, "min": 1, "max": 100}),
                "reasoning_effort": (list(REASONING_EFFORTS), {"default": "none"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("caption", "info")
    FUNCTION = "caption"
    CATEGORY = "WepeNerd/Local AI/Advanced"

    def caption(
        self, config, video, instruction, caption_style, video_mode, sampling_mode,
        sample_frames, sample_fps, max_frames, max_tokens, temperature, seed,
        system_prompt_override="", caption_prefix="", banned_phrases="",
        image_max_edge=1024, jpeg_quality=90, reasoning_effort="none",
    ):
        return _caption_video_request(
            config, video, instruction, caption_style, video_mode, sampling_mode,
            sample_frames, sample_fps, max_frames, max_tokens, temperature, seed,
            system_prompt_override, caption_prefix, banned_phrases,
            image_max_edge, jpeg_quality, reasoning_effort,
        )


class WN_LocalAIModel:
    """Simple Local AI model selector using safe lifecycle and performance defaults."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (_model_choices(),),
                "projector": (_clean_projector_choices(),),
            }
        }

    RETURN_TYPES = ("GGUF_LLM_CONFIG",)
    RETURN_NAMES = ("model",)
    FUNCTION = "build"
    CATEGORY = "WepeNerd/Local AI"

    def build(self, model, projector="Auto / None"):
        if model.startswith("<"):
            raise RuntimeError("No Local AI models found in ComfyUI/models/LLM")
        config = WNGGUFConfig(
            model_path=resolve_choice(model),
            mmproj_path=(
                None if projector in ("", "Auto / None", "(none)")
                else resolve_choice(projector, projector=True)
            ),
            server_executable="auto",
            context_size=8192,
            gpu_layers=-1,
            target_free_vram_mb=24576,
            release_after_generate=True,
            flash_attn="auto",
            cache_type_k="f16",
            cache_type_v="f16",
        )
        config.validate()
        return (config,)


class WN_PromptEnhancer:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("GGUF_LLM_CONFIG",),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "skill": (["H3", "Krea 2", "Custom"],),
            },
            "optional": {
                "system_prompt_override": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("enhanced_prompt",)
    FUNCTION = "enhance"
    CATEGORY = "WepeNerd/Local AI"

    def enhance(self, model, prompt, skill="H3", system_prompt_override=""):
        _validate_request(model, prompt, 2048)
        style = {"H3": "minimax_h3", "Krea 2": "krea2", "Custom": "custom"}.get(skill)
        if style is None:
            raise ValueError(f"Unknown Prompt Enhancer skill: {skill}")
        system_prompt = _style_prompt(style, PROMPT_STYLES, system_prompt_override)
        payload = _make_payload(
            prompt, system_prompt, 2048, 0.2, 0.8, 20, 0.0,
            1.05, 0.0, 0.0, 0, "none",
        )
        return (_run_payloads(model, [payload])[0],)


_CLEAN_IMAGE_STYLES = {
    "Dataset": "dataset_natural",
    "Detailed": "detailed_visual",
    "Short": "short",
    "Tags": "booru_tags",
    "Custom": "custom",
}


class WN_ImageCaptioner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("GGUF_LLM_CONFIG",),
                "image": ("IMAGE",),
                "style": (list(_CLEAN_IMAGE_STYLES),),
            },
            "optional": {
                "instruction": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("captions",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "caption"
    CATEGORY = "WepeNerd/Local AI"

    def caption(self, model, image, style="Dataset", instruction=""):
        caption_style = _CLEAN_IMAGE_STYLES.get(style)
        if caption_style is None:
            raise ValueError(f"Unknown Image Captioner style: {style}")
        effective_instruction = instruction.strip() or "Describe this image accurately and in detail."
        override = instruction.strip() if style == "Custom" else ""
        return WN_GGUFCaptionImage().caption(
            model, image, effective_instruction, 512, 0.2, 0,
            caption_style, "none", override, "", "", 1024, 90,
        )


_CLEAN_VIDEO_STYLES = {
    "Dataset": "dataset_natural",
    "Detailed": "detailed_visual",
    "Motion + Camera": "motion_camera",
    "Short": "short",
    "Custom": "custom",
}


class WN_VideoCaptioner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("GGUF_LLM_CONFIG",),
                "video": ("VIDEO",),
                "style": (list(_CLEAN_VIDEO_STYLES),),
            },
            "optional": {
                "instruction": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("captions",)
    FUNCTION = "caption"
    CATEGORY = "WepeNerd/Local AI"

    def caption(self, model, video, style="Dataset", instruction=""):
        caption_style = _CLEAN_VIDEO_STYLES.get(style)
        if caption_style is None:
            raise ValueError(f"Unknown Video Captioner style: {style}")
        effective_instruction = instruction.strip() or (
            "Describe this video accurately, including subjects, actions, state changes, object "
            "motion, camera movement, framing changes, environment, lighting or weather where "
            "visible, and beginning-to-end progression. Do not invent dialogue or audio."
        )
        override = instruction.strip() if style == "Custom" else ""
        caption, _info = _caption_video_request(
            model, video, effective_instruction, caption_style, "auto", "uniform",
            12, 2.0, 24, 512, 0.2, 0, override, "", "", 1024, 90, "none",
        )
        return (caption,)


class WN_GGUFLLMRelease:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "optional": {"trigger": ("STRING", {"default": ""})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "release"
    CATEGORY = "WepeNerd/Local AI/Advanced"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def release(self, trigger=""):
        pid = SERVER_MANAGER.stop()
        return ((f"Released llama-server PID {pid}." if pid else "No managed llama-server process is running."),)


class WN_GGUFLLMStatus:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "status"
    CATEGORY = "WepeNerd/Local AI/Advanced"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def status(self):
        handle = SERVER_MANAGER.current()
        if not handle:
            return ("No managed llama-server process is running.",)
        remaining = "manual"
        if handle.idle_deadline:
            remaining = f"{max(0, handle.idle_deadline - time.monotonic()):.0f}s"
        modalities = ",".join(name for name, enabled in handle.modalities.items() if enabled) or "text"
        healthy = SERVER_MANAGER.health(handle)
        return (
            f"PID={handle.process.pid}; model={os.path.basename(handle.model_path)}; "
            f"endpoint={handle.base_url}; healthy={healthy}; modalities={modalities}; "
            f"keep_alive={remaining}",
        )


NODE_CLASS_MAPPINGS = {
    "WN_LocalAIModel": WN_LocalAIModel,
    "WN_PromptEnhancer": WN_PromptEnhancer,
    "WN_ImageCaptioner": WN_ImageCaptioner,
    "WN_VideoCaptioner": WN_VideoCaptioner,
    "WN_GGUFLLMConfig": WN_GGUFLLMConfig,
    "WN_GGUFLLMGenerate": WN_GGUFLLMGenerate,
    "WN_GGUFPromptEnhance": WN_GGUFPromptEnhance,
    "WN_GGUFCaptionImage": WN_GGUFCaptionImage,
    "WN_GGUFCaptionVideo": WN_GGUFCaptionVideo,
    "WN_GGUFLLMRelease": WN_GGUFLLMRelease,
    "WN_GGUFLLMStatus": WN_GGUFLLMStatus,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "WN_LocalAIModel": "Local AI Model",
    "WN_PromptEnhancer": "Prompt Enhancer",
    "WN_ImageCaptioner": "Image Captioner",
    "WN_VideoCaptioner": "Video Captioner",
    "WN_GGUFLLMConfig": "Local AI Model (Advanced)",
    "WN_GGUFLLMGenerate": "Local AI Generate",
    "WN_GGUFPromptEnhance": "Prompt Enhancer (Advanced)",
    "WN_GGUFCaptionImage": "Image Captioner (Advanced)",
    "WN_GGUFCaptionVideo": "Video Captioner (Advanced)",
    "WN_GGUFLLMRelease": "Unload Local AI Model",
    "WN_GGUFLLMStatus": "Local AI Status",
}
