#!/usr/bin/env python3
"""Send one professional test alert to Telegram (for GitHub Actions or local)."""

from signal_builder import entry_long
from telegram_client import send_signal

if __name__ == "__main__":
    signal = entry_long(
        "RELIANCE",
        "GitHub Actions Test",
        2500.00,
        2485.00,
        rr1=1.5,
        rr2=2.0,
        best_rr=2.0,
        note="Test from GitHub automation. If you see this, secrets and Telegram are working.",
        timeframe="Test",
    )
    if not signal:
        raise SystemExit("Failed to build test signal.")
    ok = send_signal(signal)
    raise SystemExit(0 if ok else 1)
