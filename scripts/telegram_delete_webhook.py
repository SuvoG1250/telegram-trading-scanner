#!/usr/bin/env python3
"""Ensure Telegram getUpdates works (delete webhook if set)."""

from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import TELEGRAM_TOKEN


def main() -> int:
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN missing")
        return 1
    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
        params={"drop_pending_updates": "false"},
        timeout=20,
    )
    print(r.json())
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
