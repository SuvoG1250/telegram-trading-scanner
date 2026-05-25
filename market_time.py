"""Indian Standard Time helpers for session windows."""

from __future__ import annotations

from datetime import datetime, time

import pytz

from config import (
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


def is_chaitu_session(dt: datetime | None = None) -> bool:
    """Setup 3 (Chaitu50c): Pine session 09:15–15:00 IST (no new trades after 3 PM)."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    return is_new_trade_window(dt) and _after((9, 15), ist_time_tuple(dt))


def is_core_price_action_window(dt: datetime | None = None) -> bool:
    """Setup 2: 5m/15m price action — after 10:30 to avoid mixing with Setup 1."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((10, 30), t)


def is_nifty_btst_window(dt: datetime | None = None) -> bool:
    """3:20 PM – 3:30 PM IST — Nifty BTST research alert before close."""
    dt = dt or now_ist()
    if not is_weekday(dt) or not is_market_open(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((15, 20), t) and _before((15, 30), t)


def is_session_stop_window(dt: datetime | None = None) -> bool:
    """First run after 3:30 PM IST sends the daily stop alert (until 10 PM)."""
    dt = dt or now_ist()
    if not is_weekday(dt):
        return False
    t = ist_time_tuple(dt)
    return _after((MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE), t) and _before((22, 0), t)
