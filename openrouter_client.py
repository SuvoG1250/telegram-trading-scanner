"""OpenRouter API — many models including free :free tier."""

from __future__ import annotations

from config import OPENROUTER_API_KEY, OPENROUTER_MODEL
from openai_chat import chat_completion

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL_FALLBACKS = (
    "nvidia/nemotron-nano-9b-v2:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
)
_EXTRA_HEADERS = {
    "HTTP-Referer": "https://github.com/SuvoG1250/telegram-trading-scanner",
    "X-Title": "Telegram Trading Scanner",
}


def openrouter_available() -> bool:
    return bool(OPENROUTER_API_KEY)


def openrouter_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> str:
    models: list[str] = []
    if OPENROUTER_MODEL:
        models.append(OPENROUTER_MODEL)
    for m in _MODEL_FALLBACKS:
        if m not in models:
            models.append(m)
    return chat_completion(
        url=_OPENROUTER_URL,
        api_key=OPENROUTER_API_KEY,
        models=models,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_headers=_EXTRA_HEADERS,
        provider_label="OpenRouter",
    )
