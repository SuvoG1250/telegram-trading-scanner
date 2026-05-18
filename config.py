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
PREMARKET_END = (9, 26)
VOLUME_MULTIPLIER_STRICT = 2.0
VOLUME_MULTIPLIER_EARLY = 1.0
ORB_MIN_TIME = (10, 0)
MOMENTUM_ENTRY_START = (9, 30)
MOMENTUM_ENTRY_END = (9, 45)
MOMENTUM_MIN_TIMEFRAMES = 3  # of 5 TFs must agree (Strong Buy/Sell)

# Consolidation / sector strategy (Nifty 100 large-cap)
CONSOLIDATION_ENTRY_START = (9, 18)
CONSOLIDATION_ENTRY_END = (9, 36)
MIN_STOCK_PRICE = 100.0
MIN_AVG_VOLUME = 500_000
MIN_ATR_PCT = 1.0
MAX_SL_RISK_PCT = 2.5
CONSOLIDATION_RANGE_PCT = 8.0
SECTOR_ST_MIN_BULLISH_PCT = 0.5

WATCHLIST_MIN = 1
WATCHLIST_MAX = 100
# Scan full Nifty 100 universe (all that pass filters)
SCAN_FULL_UNIVERSE = True
# Long-term picks + sector report once per day at pre-market
SEND_LONG_TERM_PICKS_DAILY = True
# Minimum entry strategies that must agree before BUY/SELL alert is sent
MIN_STRATEGIES_TO_CONFIRM = 2
# Minimum profit % to best target (entry → target) required to send alert
MIN_TARGET_PROFIT_PCT = 1.0
# After morning watchlist is set, do not add new stocks during the session
LOCK_WATCHLIST_FOR_DAY = True
NEAR_52W_HIGH_PCT = 3.0
VOLUME_MULTIPLIER = 2.0  # alias for strict session
GAP_THRESHOLD_PCT = 0.5

SCAN_INTERVAL_MIN = 5
# Notify Telegram when automatic scan finds no confirmed signal (every scan)
NO_SIGNAL_STATUS_ON_AUTO_SCAN = True
SUPERTREND_LENGTH = 7
SUPERTREND_MULTIPLIER = 3.0

DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", "data"))
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
SIGNALS_FILE = DATA_DIR / "signals_sent.json"
SESSION_FILE = DATA_DIR / "session.json"

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
