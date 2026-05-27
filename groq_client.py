"""Groq chat API (OpenAI-compatible)."""

from __future__ import annotations

from config import GROQ_API_KEY, GROQ_MODEL
from openai_chat import chat_completion

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODEL_FALLBACKS = (
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
)


def groq_available() -> bool:
    return bool(GROQ_API_KEY)


def groq_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> str:
    models: list[str] = []
    if GROQ_MODEL:
        models.append(GROQ_MODEL)
    for m in _MODEL_FALLBACKS:
        if m not in models:
            models.append(m)
    return chat_completion(
        url=_GROQ_URL,
        api_key=GROQ_API_KEY,
        models=models,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        provider_label="Groq",
    )
