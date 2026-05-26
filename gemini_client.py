"""Shared Google Gemini API client (no extra dependencies)."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_MODEL_FALLBACKS = (
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
)


def gemini_available() -> bool:
    return bool(GEMINI_API_KEY)


def gemini_generate(
    prompt: str,
    *,
    max_tokens: int = 220,
    temperature: float = 0.3,
) -> str:
    if not GEMINI_API_KEY:
        return ""

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
                return text
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (404, 429):
                continue
        except Exception as exc:
            last_err = exc
            continue

    if last_err:
        logger.warning("Gemini request failed: %s", last_err)
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
    # Loose recovery: {"key": [...]} substrings
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
    logger.warning("Gemini JSON parse failed.")
    return None
