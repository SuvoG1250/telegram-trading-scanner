"""GitHub Models inference API (PAT with models scope)."""

from __future__ import annotations

from config import GH_MODELS_MODEL, GH_MODELS_TOKEN
from openai_chat import chat_completion

_GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
_MODEL_FALLBACKS = (
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
)
_EXTRA_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def github_models_available() -> bool:
    return bool(GH_MODELS_TOKEN)


def github_models_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> str:
    models: list[str] = []
    if GH_MODELS_MODEL:
        models.append(GH_MODELS_MODEL)
    for m in _MODEL_FALLBACKS:
        if m not in models:
            models.append(m)
    return chat_completion(
        url=_GITHUB_MODELS_URL,
        api_key=GH_MODELS_TOKEN,
        models=models,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        extra_headers=_EXTRA_HEADERS,
        provider_label="GitHub Models",
    )
