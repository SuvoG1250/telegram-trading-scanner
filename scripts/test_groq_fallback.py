#!/usr/bin/env python3
"""Verify Groq fallback path (needs GROQ_API_KEY in .env)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import GROQ_API_KEY  # noqa: E402
from groq_client import groq_generate  # noqa: E402
from gemini_client import gemini_generate, llm_available  # noqa: E402


def main() -> int:
    print("LLM available:", llm_available())
    if GROQ_API_KEY:
        text = groq_generate("Reply with exactly: GROQ_OK", max_tokens=20)
        print("Groq direct:", text or "(empty)")
    else:
        print("Set GROQ_API_KEY in .env to test Groq.")
    text = gemini_generate("Reply with one word: HELLO", max_tokens=20)
    print("gemini_generate (Gemini or fallback):", text or "(empty)")
    return 0 if text else 1


if __name__ == "__main__":
    raise SystemExit(main())
