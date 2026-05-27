"""Cerebras Inference API (OpenAI-compatible, high free-tier volume)."""

from __future__ import annotations

from config import CEREBRAS_API_KEY, CEREBRAS_MODEL
from openai_chat import chat_completion

_CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
_MODEL_FALLBACKS = (
    "llama-3.3-70b",
    "llama3.1-8b",
    "gpt-oss-120b",
)


def cerebras_available() -> bool:
    return bool(CEREBRAS_API_KEY)


def cerebras_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> str:
    models: list[str] = []
    if CEREBRAS_MODEL:
        models.append(CEREBRAS_MODEL)
    for m in _MODEL_FALLBACKS:
        if m not in models:
            models.append(m)
    return chat_completion(
        url=_CEREBRAS_URL,
        api_key=CEREBRAS_API_KEY,
        models=models,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        provider_label="Cerebras",
    )
