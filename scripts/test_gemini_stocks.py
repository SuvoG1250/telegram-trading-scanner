#!/usr/bin/env python3
"""Quick check: Gemini stock selection + alert note."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import GEMINI_API_KEY  # noqa: E402
from stock_gemini import (  # noqa: E402
    build_alert_ai_note,
    run_premarket_stock_selection,
)


def main() -> int:
    if not GEMINI_API_KEY:
        print("Set GEMINI_API_KEY in .env")
        return 1
    rows = [
        {"symbol": "RELIANCE", "sector": "Energy", "pct_change": 1.2, "sources": ["C:Top10"]},
        {"symbol": "TATASTEEL", "sector": "Metals", "pct_change": -2.1, "sources": ["D:Volatile"]},
    ]
    block = run_premarket_stock_selection(rows)
    print("Premarket block:", block or "(empty)")
    note = build_alert_ai_note(
        symbol="TATASTEEL",
        side="SELL",
        strategy="EMA20 + Supertrend Bearish",
        entry=145.0,
        stop_loss=146.5,
        target=142.0,
        timeframe="5m",
    )
    print("Alert note:", note or "(empty)")
    return 0 if block or note else 1


if __name__ == "__main__":
    raise SystemExit(main())
