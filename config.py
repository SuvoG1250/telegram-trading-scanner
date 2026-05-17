"""Configuration and constants for the intraday scanner."""

from __future__ import annotations

import os
from pathlib import Path

# Local secrets: .env is gitignored — never commit real tokens.
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# Indian market (NSE) session in IST
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

PREMARKET_START = (9, 10)
PREMARKET_END = (9, 25)
ORB_MIN_TIME = (10, 0)

WATCHLIST_MIN = 3
WATCHLIST_MAX = 5
NEAR_52W_HIGH_PCT = 3.0
VOLUME_MULTIPLIER = 2.0
GAP_THRESHOLD_PCT = 0.5

SCAN_INTERVAL_MIN = 5
SUPERTREND_LENGTH = 7
SUPERTREND_MULTIPLIER = 3.0

DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", "data"))
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
SIGNALS_FILE = DATA_DIR / "signals_sent.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_GROUP_CHAT_ID = os.environ.get("TELEGRAM_GROUP_CHAT_ID", "")


def telegram_chat_ids() -> list[str]:
    """All destinations for alerts (group + private DMs)."""
    ids: list[str] = []
    for raw in (
        TELEGRAM_GROUP_CHAT_ID,
        TELEGRAM_CHAT_ID,
        os.environ.get("TELEGRAM_CHAT_IDS", ""),
    ):
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if part and part not in ids:
                ids.append(part)
    return ids


YFINANCE_SUFFIX = ".NS"
