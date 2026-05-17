"""Indian Standard Time helpers for session windows."""

from __future__ import annotations

from datetime import datetime, time

import pytz

from config import (
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
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
