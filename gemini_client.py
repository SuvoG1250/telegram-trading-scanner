"""LLM client: Google Gemini primary, Groq fallback on quota/errors."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from config import GEMINI_API_KEY, GEMINI_MODEL, GROQ_FALLBACK_ENABLED

logger = logging.getLogger(__name__)

_MODEL_FALLBACKS = (
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
)


def gemini_available() -> bool:
    return bool(GEMINI_API_KEY)


def llm_available() -> bool:
    """True if Gemini and/or Groq can serve requests."""
    from groq_client import groq_available

    return gemini_available() or groq_available()


def _gemini_only_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> tuple[str, bool]:
    """Returns (text, should_try_groq). should_try_groq=True on quota/total failure."""
    if not GEMINI_API_KEY:
        return "", True

    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
    ).encode("utf-8")

    models: list[str] = []
    if GEMINI_MODEL:
        models.append(GEMINI_MODEL)
    for m in _MODEL_FALLBACKS:
        if m not in models:
            models.append(m)

    saw_quota = False
    last_err: Exception | None = None
    for model in models:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            parts = data["candidates"][0]["content"]["parts"]
            text = parts[0].get("text", "").strip()
            if text:
                return text, False
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code == 429:
                saw_quota = True
            if exc.code in (404, 429):
                continue
            return "", True
        except Exception as exc:
            last_err = exc
            continue

    if last_err:
        logger.warning("Gemini request failed: %s", last_err)
    return "", saw_quota or last_err is not None


def gemini_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> str:
    text = ""
    try_groq = False

    if GEMINI_API_KEY:
        text, try_groq = _gemini_only_generate(
            prompt, max_tokens=max_tokens, temperature=temperature
        )
        if text:
            return text

    if GROQ_FALLBACK_ENABLED and (try_groq or not GEMINI_API_KEY):
        from groq_client import groq_generate

        groq_text = groq_generate(
            prompt, max_tokens=max_tokens, temperature=temperature
        )
        if groq_text:
            return groq_text

    return ""


def _extract_json_blob(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        return fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def gemini_json(prompt: str, *, max_tokens: int = 400) -> dict | list | None:
    raw = gemini_generate(
        prompt + "\n\nReply with ONE JSON object only. No markdown fences.",
        max_tokens=max_tokens,
        temperature=0.2,
    )
    if not raw:
        return None
    blob = _extract_json_blob(raw)
    try:
        parsed = json.loads(blob)
        if isinstance(parsed, (dict, list)):
            return parsed
    except json.JSONDecodeError:
        pass
    out: dict[str, Any] = {}
    for key in ("focus", "priority", "skip", "avoid"):
        m = re.search(rf'"{key}"\s*:\s*\[(.*?)\]', raw, re.DOTALL)
        if m:
            items = re.findall(r'"([A-Za-z0-9.&-]+)"', m.group(1))
            if items:
                out[key] = items
    sm = re.search(r'"(?:summary|reason)"\s*:\s*"([^"]{5,400})"', raw)
    if sm:
        out["summary"] = sm.group(1)
    if out:
        return out
    logger.warning("LLM JSON parse failed.")
    return None
