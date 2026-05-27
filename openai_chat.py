"""Shared OpenAI-compatible chat/completions client."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def chat_completion(
    *,
    url: str,
    api_key: str,
    models: list[str],
    prompt: str,
    max_tokens: int = 220,
    temperature: float = 0.3,
    extra_headers: dict[str, str] | None = None,
    provider_label: str = "LLM",
) -> str:
    if not api_key or not models:
        return ""

    body_base = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)

    last_err: Exception | None = None
    for model in models:
        payload = json.dumps({**body_base, "model": model}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read())
            text = (data["choices"][0]["message"].get("content") or "").strip()
            if text:
                logger.info("%s OK (model=%s).", provider_label, model)
                return text
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code in (404, 429, 403, 503):
                continue
            logger.warning("%s HTTP %s model=%s.", provider_label, exc.code, model)
            continue
        except Exception as exc:
            last_err = exc
            continue

    if last_err:
        logger.warning("%s request failed: %s", provider_label, last_err)
    return ""
