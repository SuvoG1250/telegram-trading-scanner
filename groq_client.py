"""Groq chat API fallback when Gemini quota or models fail."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

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
    if not GROQ_API_KEY:
        return ""

    models: list[str] = []
    if GROQ_MODEL:
        models.append(GROQ_MODEL)
    for m in _MODEL_FALLBACKS:
        if m not in models:
            models.append(m)

    body_base = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    last_err: Exception | None = None
    for model in models:
        payload = json.dumps({**body_base, "model": model}).encode("utf-8")
        req = urllib.request.Request(
            _GROQ_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            text = (data["choices"][0]["message"].get("content") or "").strip()
            if text:
                logger.info("Groq fallback OK (model=%s).", model)
                return text
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (404, 429, 503):
                continue
            logger.warning("Groq HTTP %s for model %s.", exc.code, model)
            continue
        except Exception as exc:
            last_err = exc
            continue

    if last_err:
        logger.warning("Groq request failed: %s", last_err)
    return ""
