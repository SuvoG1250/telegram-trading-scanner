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
        # Assign so repo `.env` wins over stale shell exports (common Fyers -15 cause).
        os.environ[key.strip()] = value.strip().strip('"').strip("'")

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
MIN_STOCK_PRICE = float(os.environ.get("MIN_STOCK_PRICE", "100"))
MAX_STOCK_PRICE = float(os.environ.get("MAX_STOCK_PRICE", "1500"))
MIN_AVG_VOLUME = 500_000
MIN_ATR_PCT = 1.0
MAX_SL_RISK_PCT = 2.5
CONSOLIDATION_RANGE_PCT = 8.0
SECTOR_ST_MIN_BULLISH_PCT = 0.5

WATCHLIST_MIN = 1
WATCHLIST_MAX = 100
# nifty500 | nifty100 | nse_price_band (all NSE EQ with last close in MIN/MAX price)
SCAN_UNIVERSE_MODE = os.environ.get("SCAN_UNIVERSE_MODE", "nse_price_band")
# Intraday: scan every symbol in get_scan_universe(), not only top-ranked playbook picks
SCAN_ALL_UNIVERSE_INTRADAY = True
# Scan full universe (refresh small legacy watchlists)
SCAN_FULL_UNIVERSE = True
# Chaitu50c (Pine port) — chart interval for yfinance replay
CHAITU_INTERVAL = os.environ.get("CHAITU_INTERVAL", "15m")
CHAITU_ENHANCED_MODE = os.environ.get("CHAITU_ENHANCED_MODE", "true").lower() in ("1", "true", "yes")
# Compact BUY/SELL lines only (operational messages use flags below)
SIGNALS_ONLY_TELEGRAM = os.environ.get("SIGNALS_ONLY_TELEGRAM", "true").lower() in (
    "1",
    "true",
    "yes",
)
# Operational Telegram (independent of SIGNALS_ONLY_TELEGRAM)
SEND_PREMARKET_REPORT = os.environ.get("SEND_PREMARKET_REPORT", "false").lower() in (
    "1",
    "true",
    "yes",
)
SEND_SESSION_ALERTS = os.environ.get("SEND_SESSION_ALERTS", "true").lower() in (
    "1",
    "true",
    "yes",
)
SEND_BOOT_ALERT = os.environ.get("SEND_BOOT_ALERT", "false").lower() in ("1", "true", "yes")
BOOT_DELAY_MINUTES = int(os.environ.get("BOOT_DELAY_MINUTES", "30"))
# all/stocks = 5 setups (no Chaitu50c) | ema | ema20_st | chaitu (legacy only)
SCAN_STRATEGIES = os.environ.get("SCAN_STRATEGIES", "all").lower()
# 9/15 EMA crossover (5m)
EMA_FAST = int(os.environ.get("EMA_FAST", "9"))
EMA_SLOW = int(os.environ.get("EMA_SLOW", "15"))
# 9/21 EMA crossover (5m)
EMA21_FAST = int(os.environ.get("EMA21_FAST", "9"))
EMA21_SLOW = int(os.environ.get("EMA21_SLOW", "21"))
EMA_INTERVAL = os.environ.get("EMA_INTERVAL", "15m")
EMA_VOLUME_MULTIPLIER = float(os.environ.get("EMA_VOLUME_MULTIPLIER", "1.5"))
EMA_MAX_SL_PCT = float(os.environ.get("EMA_MAX_SL_PCT", "0.5"))
EMA_MIN_TARGET_PROFIT_PCT = float(os.environ.get("EMA_MIN_TARGET_PROFIT_PCT", "2.0"))
EMA_MAX_TARGET_PROFIT_PCT = float(os.environ.get("EMA_MAX_TARGET_PROFIT_PCT", "3.0"))
EMA_RISK_REWARD = float(os.environ.get("EMA_RISK_REWARD", "2.0"))
RISK_PER_TRADE_INR = float(os.environ.get("RISK_PER_TRADE_INR", "1000"))
SEND_LONG_TERM_PICKS_DAILY = os.environ.get("SEND_LONG_TERM_PICKS_DAILY", "true").lower() in (
    "1",
    "true",
    "yes",
)
# Stock alert: 1 = any single strategy (Chaitu or EMA); 2+ = must agree same side
MIN_STRATEGIES_TO_CONFIRM = max(1, int(os.environ.get("MIN_STRATEGIES_TO_CONFIRM", "1")))
# Minimum profit % to best target (entry → target) required to send alert
MIN_TARGET_PROFIT_PCT = float(os.environ.get("MIN_TARGET_PROFIT_PCT", "2.0"))
# Cash equity playbook caps SL at 0.6% — best target ~1.2% at 1:2 R:R
MIN_EQUITY_TARGET_PROFIT_PCT = float(os.environ.get("MIN_EQUITY_TARGET_PROFIT_PCT", "1.0"))
# Only alert names that often trade with enough range for ~2–3% intraday moves
MIN_STOCK_MOVE_POTENTIAL_PCT = float(os.environ.get("MIN_STOCK_MOVE_POTENTIAL_PCT", "2.0"))
REQUIRE_FNO_ELIGIBLE = os.environ.get("REQUIRE_FNO_ELIGIBLE", "true").lower() in (
    "1",
    "true",
    "yes",
)
REQUIRE_INTRADAY_MARGIN = os.environ.get("REQUIRE_INTRADAY_MARGIN", "true").lower() in (
    "1",
    "true",
    "yes",
)
APPLY_QUALITY_FILTER = os.environ.get("APPLY_QUALITY_FILTER", "true").lower() in (
    "1",
    "true",
    "yes",
)
USE_TRADE_FILTERS = os.environ.get("USE_TRADE_FILTERS", "true").lower() in ("1", "true", "yes")
# Module 3 — universal risk (cash equity)
MAX_SL_PCT_PLAYBOOK = 0.6  # % of price — stop may not be wider than this
MIN_RISK_REWARD_PLAYBOOK = 2.0  # minimum 1:2 to best target
# Method D — Nifty 500-style scan: flag names with large prior-day % move
VOLATILE_SCAN_MIN_PCT = 4.0
VOLATILE_SCAN_MAX_PCT = 8.0
PLAYBOOK_TRAIL_NOTE = (
    "Book 70–80% at 1R target; trail 20–30% runner on 10 EMA (5m): exit only if a candle "
    "CLOSES below 10 EMA AND the next candle breaks the low of that candle. Square intraday by 3:30 PM IST."
)
# After morning watchlist is set, do not add new stocks during the session
LOCK_WATCHLIST_FOR_DAY = True
NEAR_52W_HIGH_PCT = 3.0
VOLUME_MULTIPLIER = 2.0  # alias for strict session
GAP_THRESHOLD_PCT = 0.5

SCAN_INTERVAL_MIN = 5
# One-line Telegram after each scan (symbols scanned, 2-strategy count, BUY sent)
SEND_SCAN_SUMMARY = os.environ.get("SEND_SCAN_SUMMARY", "false").lower() in (
    "1",
    "true",
    "yes",
)
# Notify Telegram when automatic scan finds no confirmed signal (every scan)
NO_SIGNAL_STATUS_ON_AUTO_SCAN = (
    not SIGNALS_ONLY_TELEGRAM
    and os.environ.get("NO_SIGNAL_STATUS_ON_AUTO_SCAN", "false").lower()
    in ("1", "true", "yes")
)
# Nifty options — Supertrend (Pine: ATR 10, factor 3)
# Nifty BTST — one overnight CALL/PUT alert after sentiment + news research (3:20–3:30 PM IST)
NIFTY_BTST_ENABLED = os.environ.get("NIFTY_BTST_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
# BTST CALL/PUT only when all confirmation checks pass (else risky warning)
NIFTY_BTST_MIN_SCORE = float(os.environ.get("NIFTY_BTST_MIN_SCORE", "2.5"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NIFTY_OPTIONS_ENABLED = os.environ.get("NIFTY_OPTIONS_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
NIFTY_ST_ATR_LENGTH = int(os.environ.get("NIFTY_ST_ATR_LENGTH", "10"))
NIFTY_ST_FACTOR = float(os.environ.get("NIFTY_ST_FACTOR", "3.0"))
NIFTY_ST_INTERVAL = os.environ.get("NIFTY_ST_INTERVAL", "5m")
NIFTY_STRIKE_STEP = int(os.environ.get("NIFTY_STRIKE_STEP", "50"))
NIFTY_OPTION_PREMIUM_SL_PCT = float(os.environ.get("NIFTY_OPTION_PREMIUM_SL_PCT", "30"))
NIFTY_OPTION_PREMIUM_ATR_FACTOR = float(os.environ.get("NIFTY_OPTION_PREMIUM_ATR_FACTOR", "0.45"))
# Fixed ₹ risk on option premium (when SL_POINTS > 0, overrides %-based SL)
NIFTY_OPTION_PREMIUM_SL_POINTS = float(os.environ.get("NIFTY_OPTION_PREMIUM_SL_POINTS", "15"))
NIFTY_OPTION_PREMIUM_TARGET_POINTS = float(os.environ.get("NIFTY_OPTION_PREMIUM_TARGET_POINTS", "30"))
NIFTY_OPTION_PREMIUM_TRAIL_MAX_POINTS = float(os.environ.get("NIFTY_OPTION_PREMIUM_TRAIL_MAX_POINTS", "100"))
# tv = TradingView ta.supertrend; exit490 = Pine ST+TSL (ATR×mult on hl2, bars default 1)
NIFTY_ST_ENGINE = os.environ.get("NIFTY_ST_ENGINE", "exit490").lower()
NIFTY_EXIT490_ATR_BARS = int(os.environ.get("NIFTY_EXIT490_ATR_BARS", "1"))
NIFTY_EXIT490_ATR_MULT = float(os.environ.get("NIFTY_EXIT490_ATR_MULT", "3.0"))
# Dhan HQ v2 — live Nifty option premium (optional; falls back to ATR estimate)
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")
DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
# true = token from developer.dhanhq.co (Sandbox); false = token from web.dhan.co (Live)
DHAN_SANDBOX = os.environ.get("DHAN_SANDBOX", "false").lower() in ("1", "true", "yes")
NIFTY_UNDERLYING_SCRIP = int(os.environ.get("NIFTY_UNDERLYING_SCRIP", "13"))
NIFTY_UNDERLYING_SEG = os.environ.get("NIFTY_UNDERLYING_SEG", "IDX_I")
# Upstox — option chain (free with account; use Analytics → Generate Token)
UPSTOX_API_KEY = os.environ.get("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET = os.environ.get("UPSTOX_API_SECRET", "")
UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
UPSTOX_NIFTY_INSTRUMENT_KEY = os.environ.get(
    "UPSTOX_NIFTY_INSTRUMENT_KEY", "NSE_INDEX|Nifty 50"
)
# upstox | fyers | dhan | auto (fyers → upstox → dhan)
OPTION_DATA_PROVIDER = os.environ.get("OPTION_DATA_PROVIDER", "auto").lower()
# Fyers My API — App ID + access token from login (https://myapi.fyers.in/)
FYERS_APP_ID = os.environ.get("FYERS_APP_ID", os.environ.get("FYERS_CLIENT_ID", ""))
FYERS_SECRET_KEY = os.environ.get("FYERS_SECRET_KEY", "")
FYERS_ACCESS_TOKEN = os.environ.get("FYERS_ACCESS_TOKEN", "")
FYERS_NIFTY_INDEX_SYMBOL = os.environ.get("FYERS_NIFTY_INDEX_SYMBOL", "NSE:NIFTY50-INDEX")
FYERS_OPTION_STRIKE_COUNT = int(os.environ.get("FYERS_OPTION_STRIKE_COUNT", "20"))
# Must match My API app "Redirect URL" exactly (character-for-character) or login shows redirectUrl mismatch
FYERS_REDIRECT_URI = os.environ.get(
    "FYERS_REDIRECT_URI",
    "https://trade.fyers.in/api-login/redirect-uri/index.html",
)
SUPERTREND_LENGTH = 7
SUPERTREND_MULTIPLIER = 3.0

DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", "data"))
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
SIGNALS_FILE = DATA_DIR / "signals_sent.json"
SESSION_FILE = DATA_DIR / "session.json"
TRADES_JOURNAL_FILE = DATA_DIR / "trades_journal.json"
ACTIVE_POSITIONS_FILE = DATA_DIR / "active_positions.json"

# Long-side equity: max BUY Telegram alerts per IST day (best names first, spread by per-scan cap below)
MAX_DAILY_BUY_SIGNALS = max(
    5,
    min(
        10,
        int(os.environ.get("MAX_DAILY_BUY_SIGNALS", os.environ.get("MAX_STOCK_ALERTS_PER_SCAN", "10"))),
    ),
)
# Each 5m scan: emit at most this many new BUY signals (so 5–10 buys fill over the day, not in one burst)
MAX_BUY_ALERTS_PER_SCAN = max(1, min(3, int(os.environ.get("MAX_BUY_ALERTS_PER_SCAN", "1"))))
# SELL ideas: max per 5m scan (0 = equity short setups are not alerted)
MAX_SELL_ALERTS_PER_SCAN = max(0, min(5, int(os.environ.get("MAX_SELL_ALERTS_PER_SCAN", "1"))))
# Optional pings when lifecycle marks SL/Target hit before new alert rules
SLTP_CLOSE_ALERT_TELEGRAM = os.environ.get("SLTP_CLOSE_ALERT_TELEGRAM", "false").lower() in (
    "1",
    "true",
    "yes",
)

# No new trade alerts after this time (IST)
NO_NEW_TRADES_AFTER_HOUR = int(os.environ.get("NO_NEW_TRADES_AFTER_HOUR", "15"))
NO_NEW_TRADES_AFTER_MINUTE = int(os.environ.get("NO_NEW_TRADES_AFTER_MINUTE", "0"))
SEND_DAILY_SUMMARY = os.environ.get("SEND_DAILY_SUMMARY", "true").lower() in ("1", "true", "yes")

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
