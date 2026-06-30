"""NVIDIA NIM (build.nvidia.com) — OpenAI-compatible chat API."""

from __future__ import annotations

from config import NVIDIA_NIM_API_KEY, NVIDIA_NIM_MODEL
from openai_chat import chat_completion

_NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Verified hosted models — primary first (speed + quality for finance/Bengali).
_MODEL_FALLBACKS = (
    "meta/llama-3.3-70b-instruct",
    "nvidia/nemotron-3-super-120b-a12b",
    "qwen/qwen2.5-72b-instruct",
    "nvidia/llama-3.1-nemotron-nano-8b-v1",
)


def nvidia_nim_available() -> bool:
    return bool(NVIDIA_NIM_API_KEY)


def nvidia_nim_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> str:
    models: list[str] = []
    if NVIDIA_NIM_MODEL:
        models.append(NVIDIA_NIM_MODEL)
    for m in _MODEL_FALLBACKS:
        if m not in models:
            models.append(m)
    timeout = 90 if max_tokens > 800 else 55
    return chat_completion(
        url=_NIM_URL,
        api_key=NVIDIA_NIM_API_KEY,
        models=models,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        provider_label="NVIDIA NIM",
        timeout=timeout,
    )
