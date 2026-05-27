#!/usr/bin/env python3
"""Test LLM provider chain (Cerebras, GitHub Models, Groq, Gemini)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import LLM_PROVIDER_ORDER  # noqa: E402
from gemini_client import gemini_generate, llm_available  # noqa: E402


def main() -> int:
    print("LLM available:", llm_available())
    print("Order:", LLM_PROVIDER_ORDER)
    text = gemini_generate("Reply with exactly: LLM_OK", max_tokens=16)
    print("Chain result:", text or "(empty)")
    return 0 if text else 1


if __name__ == "__main__":
    raise SystemExit(main())
