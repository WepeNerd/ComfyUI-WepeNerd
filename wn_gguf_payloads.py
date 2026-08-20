"""OpenAI-compatible chat and multimodal payload helpers."""

from __future__ import annotations


REASONING_EFFORTS = ("default", "none", "low", "medium", "high")


def image_content(prompt: str, image_data_url: str) -> list[dict]:
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]


def sampled_video_content(
    prompt: str,
    image_data_urls: list[str],
    timestamps: list[float],
) -> list[dict]:
    if len(image_data_urls) != len(timestamps) or not image_data_urls:
        raise ValueError("Sampled video frames and timestamps must be non-empty and equal length")
    content = [
        {
            "type": "text",
            "text": prompt + "\n\nThese images are chronological frames from one continuous video.",
        }
    ]
    for number, (url, timestamp) in enumerate(zip(image_data_urls, timestamps), start=1):
        content.append({"type": "text", "text": f"Frame {number} — {timestamp:.2f} s"})
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def native_video_content(prompt: str, video_base64: str) -> list[dict]:
    if not video_base64:
        raise ValueError("Native video data is empty")
    return [
        {"type": "text", "text": prompt},
        {"type": "input_video", "input_video": {"data": video_base64}},
    ]


def build_chat_payload(
    prompt: str,
    system_prompt: str = "",
    image_data_url: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
    top_p: float = 0.95,
    top_k: int = 40,
    seed: int | None = None,
    repetition_penalty: float = 1.05,
    min_p: float = 0.0,
    presence_penalty: float = 0.0,
    frequency_penalty: float = 0.0,
    reasoning_effort: str = "default",
    user_content=None,
    stream: bool = True,
) -> dict:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")
    if int(max_tokens) <= 0:
        raise ValueError("max_tokens must be positive")
    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"Unknown reasoning_effort: {reasoning_effort}")
    if image_data_url and user_content is not None:
        raise ValueError("Use either image_data_url or user_content, not both")

    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    content = user_content if user_content is not None else (
        image_content(prompt, image_data_url) if image_data_url else prompt
    )
    messages.append({"role": "user", "content": content})

    payload = {
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "top_p": float(top_p),
        "top_k": int(top_k),
        "min_p": float(min_p),
        "repeat_penalty": float(repetition_penalty),
        "presence_penalty": float(presence_penalty),
        "frequency_penalty": float(frequency_penalty),
        "stream": bool(stream),
    }
    if reasoning_effort != "default":
        payload["reasoning_effort"] = reasoning_effort
    if seed is not None:
        payload["seed"] = int(seed)
    return payload
