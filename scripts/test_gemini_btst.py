#!/usr/bin/env python3
"""Quick check that GEMINI_API_KEY works for BTST summaries."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import GEMINI_API_KEY  # noqa: E402
from gemini_client import gemini_generate  # noqa: E402
from nifty_btst import _optional_gemini_summary  # noqa: E402


def main() -> int:
    if not GEMINI_API_KEY:
        print("Set GEMINI_API_KEY in .env")
        return 1
    text = _optional_gemini_summary(
        ["Nifty holds gains ahead of Fed", "Bank Nifty leads advance"],
        {"summary": "Mild bullish bias, gap up open"},
        "CALL",
    )
    if not text:
        print("Gemini returned empty — check API key / billing / model name.")
        return 1
    print("Gemini OK — sample BTST summary:")
    print(text[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
