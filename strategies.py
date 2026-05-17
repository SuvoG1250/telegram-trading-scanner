"""Part 2: Intraday strategy scanners."""

from __future__ import annotations

import logging
from typing import Literal

import pandas as pd

from config import GAP_THRESHOLD_PCT, SUPERTREND_LENGTH, SUPERTREND_MULTIPLIER
from indicators import ema, supertrend_direction
from data_fetcher import fetch_daily, fetch_intraday, today_session_df
from market_time import is_momentum_entry_window, is_orb_allowed, now_ist
from momentum_screener import analyze_multi_timeframe, first_two_15m_candles, format_breakdown
from risk import TradeLevels, levels_for_long, levels_for_short
from telegram_client import Signal

logger = logging.getLogger(__name__)

Side = Literal["BUY", "SELL"]


def _ema_cross_up(fast: pd.Series, slow: pd.Series) -> bool:
    if len(fast) < 2:
        return False
    return fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]


def _ema_cross_down(fast: pd.Series, slow: pd.Series) -> bool:
    if len(fast) < 2:
        return False
    return fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]


def winning_combination(symbol: str) -> Signal | None:
    df = fetch_intraday(symbol, "5m")
    session = today_session_df(df, now_ist().date())
    if len(session) < 25:
        return None

    session = session.copy()
    session["EMA5"] = ema(session["Close"], 5)
    session["EMA20"] = ema(session["Close"], 20)
    session["ST_DIR"] = supertrend_direction(
        session, length=SUPERTREND_LENGTH, multiplier=SUPERTREND_MULTIPLIER
    )

    fast, slow, direction = session["EMA5"], session["EMA20"], session["ST_DIR"]
    last = session.iloc[-1]
    entry = float(last["Close"])

    if _ema_cross_up(fast, slow) and direction.iloc[-1] > 0:
        levels = levels_for_long(entry, float(last["Low"]))
        return Signal(symbol, "The Winning Combination", "BUY", levels)

    if _ema_cross_down(fast, slow) and direction.iloc[-1] < 0:
        levels = levels_for_short(entry, float(last["High"]))
        return Signal(symbol, "The Winning Combination", "SELL", levels)

    return None


def _orb_range(session_15m: pd.DataFrame) -> tuple[float, float] | None:
    """High/Low of 2nd (9:30-9:45) and 3rd (9:45-10:00) 15-min candles."""
    segment = session_15m.between_time("09:30", "10:00")
    if len(segment) < 2:
        after_open = session_15m.between_time("09:15", "10:00")
        if len(after_open) < 3:
            return None
        segment = after_open.iloc[1:3]
    else:
        segment = segment.iloc[:2]
    return float(segment["High"].max()), float(segment["Low"].min())


def orb_15min(symbol: str) -> Signal | None:
    if not is_orb_allowed():
        return None

    df = fetch_intraday(symbol, "15m")
    session = today_session_df(df, now_ist().date())
    orb = _orb_range(session)
    if orb is None:
        return None
    combined_high, combined_low = orb
    last_close = float(session.iloc[-1]["Close"])
    last = session.iloc[-1]

    if last_close > combined_high:
        levels = levels_for_long(last_close, float(last["Low"]))
        note = f"ORB break above {combined_high:.2f}"
        return Signal(symbol, "15-Min ORB", "BUY", levels, note=note)

    if last_close < combined_low:
        levels = levels_for_short(last_close, float(last["High"]))
        note = f"ORB break below {combined_low:.2f}"
        return Signal(symbol, "15-Min ORB", "SELL", levels, note=note)

    return None


def _gap_type(daily: pd.DataFrame) -> Literal["gap_up", "gap_down", "none"]:
    if len(daily) < 3:
        return "none"
    yday = daily.iloc[-2]
    prev = daily.iloc[-3]
    y_open = float(yday["Open"])
    prev_close = float(prev["Close"])
    prev_high = float(prev["High"])
    prev_low = float(prev["Low"])
    gap_pct = abs(y_open - prev_close) / prev_close * 100 if prev_close else 0
    if gap_pct < GAP_THRESHOLD_PCT:
        return "none"
    if y_open > prev_high:
        return "gap_up"
    if y_open < prev_low:
        return "gap_down"
    if y_open > prev_close:
        return "gap_up"
    if y_open < prev_close:
        return "gap_down"
    return "none"


def gap_day_breakout(symbol: str) -> Signal | None:
    daily = fetch_daily(symbol, period="3mo")
    if len(daily) < 3:
        return None
    gap = _gap_type(daily)
    if gap == "none":
        return None

    yday = daily.iloc[-2]
    y_high = float(yday["High"])
    y_low = float(yday["Low"])

    df5 = fetch_intraday(symbol, "5m")
    session = today_session_df(df5, now_ist().date())
    if session.empty:
        return None
    last = session.iloc[-1]
    close = float(last["Close"])

    if gap == "gap_down" and close > y_high:
        levels = levels_for_long(close, float(last["Low"]))
        return Signal(
            symbol,
            "Gap Day Breakout",
            "BUY",
            levels,
            note="Gap down yesterday; reclaim above prior day high.",
        )

    if gap == "gap_up" and close < y_low:
        levels = levels_for_short(close, float(last["High"]))
        return Signal(
            symbol,
            "Gap Day Breakout",
            "SELL",
            levels,
            note="Gap up yesterday; break below prior day low.",
        )

    return None


def screener_momentum_930(symbol: str) -> Signal | None:
    """
    Investing.com-style 5-timeframe momentum entry (9:30–9:45 AM IST).

    Long: Strong Buying on most timeframes; entry at 2nd 15m candle, SL = low of 1st.
    Short: Strong Selling on most timeframes; SL = high of 1st.
    Target: 1:1 RR; exit by 3:30 PM IST.
    """
    if not is_momentum_entry_window():
        return None

    mtf = analyze_multi_timeframe(symbol)
    if not mtf or mtf["consensus"] == "mixed":
        return None

    df15 = fetch_intraday(symbol, "15m")
    session = today_session_df(df15, now_ist().date())
    candles = first_two_15m_candles(session)
    if candles is None:
        return None

    first, second = candles
    entry = float(second["Open"])
    breakdown_note = format_breakdown(mtf["breakdown"])
    exit_note = "Exit by 3:30 PM IST. T1 = 1:1 RR."

    if mtf["consensus"] == "strong_buy":
        sl = float(first["Low"])
        if entry <= sl:
            return None
        levels = levels_for_long(entry, sl, rr1=1.0, rr2=1.5)
        levels.trailing_note = "Book partial at T1 (1:1). Exit remainder by 3:30 PM IST."
        return Signal(
            symbol,
            "Screener Momentum (5 TF)",
            "BUY",
            levels,
            note=f"{breakdown_note}. {exit_note}",
        )

    if mtf["consensus"] == "strong_sell":
        sl = float(first["High"])
        if entry >= sl:
            return None
        levels = levels_for_short(entry, sl, rr1=1.0, rr2=1.5)
        levels.trailing_note = "Book partial at T1 (1:1). Exit remainder by 3:30 PM IST."
        return Signal(
            symbol,
            "Screener Momentum (5 TF)",
            "SELL",
            levels,
            note=f"{breakdown_note}. {exit_note}",
        )

    return None


STRATEGY_SCANNERS = [
    winning_combination,
    orb_15min,
    gap_day_breakout,
    screener_momentum_930,
]
