"""Indian Standard Time helpers for session windows."""

from __future__ import annotations

from datetime import datetime, time

import pytz

from config import (
    EOD_SUMMARY_AFTER_HOUR,
    EOD_SUMMARY_AFTER_MINUTE,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    NO_NEW_TRADES_AFTER_HOUR,
    NO_NEW_TRADES_AFTER_MINUTE,
    CONSOLIDATION_ENTRY_END,
    CONSOLIDATION_ENTRY_START,
    MOMENTUM_ENTRY_END,
    MOMENTUM_ENTRY_START,
    ORB_MIN_TIME,
    PREMARKET_END,
    PREMARKET_START,
    GLOBAL_ALERT_END_HOUR,
    GLOBAL_ALERT_END_MINUTE,
    GLOBAL_ALERT_START_HOUR,
    GLOBAL_ALERT_START_MINUTE,
)

IST = pytz.timezone("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def ist_time_tuple(dt: datetime | None = None) -> tuple[int, int]:
    dt = dt or now_ist()
    return dt.hour, dt.minute


def is_weekday(dt: datetime | None = None) -> bool:
    dt = dt or now_ist()
    return dt.weekday() < 5


def _after(start: tuple[int, int], current: tuple[int, int]) -> bool:
    return current >= start


def _before(end: tuple[int, int], current: tuple[int, int]) -> bool:
    return current < end


def is_market_open(dt: datetime | None = None) -> bool:
    dt = dt or now_ist()
    if not is_weekday(dt):
        return False
    t = ist_time_tuple(dt)
    open_t = (MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)
    close_t = (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
    return _after(open_t, t) and _before(close_t, t)


def is_premarket_window(dt: datetime | None = None) -> bool:
    dt = dt or now_ist()
    if not is_weekday(dt):
        return False
    t = ist_time_tuple(dt)
    return _after(PREMARKET_START, t) and _before(PREMARKET_END, t)


def is_orb_allowed(dt: datetime | None = None) -> bool:
    dt = dt or now_ist()
    return is_market_open(dt) and _after(ORB_MIN_TIME, ist_time_tuple(dt))


def today_key(dt: datetime | None = None) -> str:
    return (dt or now_ist()).strftime("%Y-%m-%d")


def is_active_session(dt: datetime | None = None) -> bool:
    """Pre-market or regular market hours (scanner should be running)."""
    return is_premarket_window(dt) or is_market_open(dt)


def is_consolidation_entry_window(dt: datetime | None = None) -> bool:
    """9:18–9:36 AM IST — 3m breakout entry window after first candles form."""
    dt = dt or now_ist()
    if not is_weekday(dt):
        return False
    t = ist_time_tuple(dt)
    return _after(CONSOLIDATION_ENTRY_START, t) and _before(CONSOLIDATION_ENTRY_END, t)


def is_momentum_entry_window(dt: datetime | None = None) -> bool:
    """9:30–9:45 AM IST — optimal window for 15m screener momentum entry."""
    dt = dt or now_ist()
    if not is_weekday(dt):
        return False
    t = ist_time_tuple(dt)
    return _after(MOMENTUM_ENTRY_START, t) and _before(MOMENTUM_ENTRY_END, t)


def is_morning_1m_playbook_window(dt: datetime | None = None) -> bool:
    """Setup 1: 9:16 AM – 10:30 AM IST (1-minute morning breakout)."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((9, 16), t) and _before((10, 30), t)


def is_new_trade_window(dt: datetime | None = None) -> bool:
    """New entry signals allowed 9:15 AM – before 3:00 PM IST."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    t = ist_time_tuple(dt)
    cutoff = (NO_NEW_TRADES_AFTER_HOUR, NO_NEW_TRADES_AFTER_MINUTE)
    return _before(cutoff, t)


def is_equity_entry_session(dt: datetime | None = None) -> bool:
    """Stock intraday entries: 9:15 AM – before 3:00 PM IST."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    return is_new_trade_window(dt) and _after((9, 15), ist_time_tuple(dt))


def is_chaitu_session(dt: datetime | None = None) -> bool:
    """Legacy Chaitu50c window (disabled in default scan)."""
    return is_equity_entry_session(dt)


def is_ema20_st_entry_window(dt: datetime | None = None) -> bool:
    """EMA20+ST bearish: after 9:30, avoid last 15m; best 9:30–11:30 & 1:30–2:30."""
    dt = dt or now_ist()
    if not is_equity_entry_session(dt):
        return False
    t = ist_time_tuple(dt)
    if not _after((9, 30), t) or not _before((15, 15), t):
        return False
    morning = _after((9, 30), t) and _before((11, 30), t)
    afternoon = _after((13, 30), t) and _before((14, 30), t)
    return morning or afternoon


def is_core_price_action_window(dt: datetime | None = None) -> bool:
    """Setup 2: 5m/15m price action — after 10:30 to avoid mixing with Setup 1."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((10, 30), t)


def is_stock_btst_window(dt: datetime | None = None) -> bool:
    """3:10 PM – 3:20 PM IST — equity BTST (fundamental + news) before Nifty BTST."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((15, 10), t) and _before((15, 20), t)


def is_nifty_btst_window(dt: datetime | None = None) -> bool:
    """3:20 PM – 3:30 PM IST — Nifty BTST research alert before close."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((15, 20), t) and _before((15, 30), t)


def is_eod_summary_due(dt: datetime | None = None) -> bool:
    """Weekday, after EOD send time (default 15:32 IST) — once-per-day P/L summary."""
    dt = dt or now_ist()
    if not is_weekday(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((EOD_SUMMARY_AFTER_HOUR, EOD_SUMMARY_AFTER_MINUTE), t) and _before((22, 0), t)


def is_session_stop_window(dt: datetime | None = None) -> bool:
    """After EOD summary window — end intraday scanner loop (until 10 PM)."""
    return is_eod_summary_due(dt)


def is_global_alert_window(dt: datetime | None = None) -> bool:
    """Global assets alerts (BTC/ETH/XAU): 07:00–23:00 IST by default."""
    dt = dt or now_ist()
    t = ist_time_tuple(dt)
    start = (GLOBAL_ALERT_START_HOUR, GLOBAL_ALERT_START_MINUTE)
    end = (GLOBAL_ALERT_END_HOUR, GLOBAL_ALERT_END_MINUTE)
    return _after(start, t) and _before(end, t)


def is_global_market_scan_allowed(dt: datetime | None = None) -> bool:
    """Global alerts only outside NSE regular session (9:15–15:30 IST weekdays)."""
    dt = dt or now_ist()
    if not is_global_alert_window(dt):
        return False
    return not is_market_open(dt)
