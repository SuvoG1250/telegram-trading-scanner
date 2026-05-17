"""Part 2: Intraday strategy scanners — all return validated professional signals."""

from __future__ import annotations

import logging
from typing import Literal

import pandas as pd

from config import GAP_THRESHOLD_PCT, SUPERTREND_LENGTH, SUPERTREND_MULTIPLIER
from consolidation import (
    check_3m_breakout_entry,
    check_9ema_trail_exit,
    consolidation_levels,
    is_consolidation_candidate,
    strong_sectors,
)
from data_fetcher import fetch_daily, fetch_intraday, today_session_df
from indicators import ema, supertrend_direction
from market_time import (
    is_consolidation_entry_window,
    is_market_open,
    is_momentum_entry_window,
    is_orb_allowed,
    now_ist,
)
from momentum_screener import analyze_multi_timeframe, first_two_15m_candles, format_breakdown
from sector_map import sector_for
from signal_builder import entry_long, entry_short, exit_signal
from state import (
    clear_consolidation_active,
    is_consolidation_active,
    mark_consolidation_active,
)
from telegram_client import Signal

logger = logging.getLogger(__name__)

Side = Literal["BUY", "SELL"]

STANDARD_EXIT = "Square off ALL positions by 3:30 PM IST."


def _ema_cross_up(fast: pd.Series, slow: pd.Series) -> bool:
    if len(fast) < 2:
        return False
    return fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]


def _ema_cross_down(fast: pd.Series, slow: pd.Series) -> bool:
    if len(fast) < 2:
        return False
    return fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]


def winning_combination(symbol: str) -> Signal | None:
    """5 EMA x 20 EMA + Supertrend(7,3) on 5-minute chart."""
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
        return entry_long(
            symbol,
            "The Winning Combination",
            entry,
            float(last["Low"]),
            note="5m: 5 EMA crossed above 20 EMA + Supertrend turned GREEN.",
            timeframe="5 Min",
        )

    if _ema_cross_down(fast, slow) and direction.iloc[-1] < 0:
        return entry_short(
            symbol,
            "The Winning Combination",
            entry,
            float(last["High"]),
            note="5m: 5 EMA crossed below 20 EMA + Supertrend turned RED.",
            timeframe="5 Min",
        )
    return None


def _orb_range(session_15m: pd.DataFrame) -> tuple[float, float] | None:
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
    """15m ORB: breakout of 9:30–10:00 range after 10:00 AM."""
    if not is_orb_allowed():
        return None

    session = today_session_df(fetch_intraday(symbol, "15m"), now_ist().date())
    orb = _orb_range(session)
    if orb is None:
        return None
    combined_high, combined_low = orb
    last = session.iloc[-1]
    close = float(last["Close"])

    if close > combined_high:
        return entry_long(
            symbol,
            "15-Min ORB",
            close,
            float(last["Low"]),
            note=f"15m close above ORB high ₹{combined_high:.2f} (9:30–10:00 range).",
            timeframe="15 Min",
        )

    if close < combined_low:
        return entry_short(
            symbol,
            "15-Min ORB",
            close,
            float(last["High"]),
            note=f"15m close below ORB low ₹{combined_low:.2f} (9:30–10:00 range).",
            timeframe="15 Min",
        )
    return None


def _gap_type(daily: pd.DataFrame) -> Literal["gap_up", "gap_down", "none"]:
    if len(daily) < 3:
        return "none"
    yday, prev = daily.iloc[-2], daily.iloc[-3]
    y_open = float(yday["Open"])
    prev_close, prev_high, prev_low = float(prev["Close"]), float(prev["High"]), float(prev["Low"])
    gap_pct = abs(y_open - prev_close) / prev_close * 100 if prev_close else 0
    if gap_pct < GAP_THRESHOLD_PCT:
        return "none"
    if y_open > prev_high or y_open > prev_close:
        return "gap_up"
    if y_open < prev_low or y_open < prev_close:
        return "gap_down"
    return "none"


def gap_day_breakout(symbol: str) -> Signal | None:
    """Gap reversal breakout on 5-minute chart."""
    daily = fetch_daily(symbol, period="3mo")
    gap = _gap_type(daily)
    if gap == "none":
        return None

    yday = daily.iloc[-2]
    y_high, y_low = float(yday["High"]), float(yday["Low"])
    session = today_session_df(fetch_intraday(symbol, "5m"), now_ist().date())
    if session.empty:
        return None
    last = session.iloc[-1]
    close = float(last["Close"])

    if gap == "gap_down" and close > y_high:
        return entry_long(
            symbol,
            "Gap Day Breakout",
            close,
            float(last["Low"]),
            note=f"Gap-down day prior; 5m reclaim above yesterday high ₹{y_high:.2f}.",
            timeframe="5 Min",
        )

    if gap == "gap_up" and close < y_low:
        return entry_short(
            symbol,
            "Gap Day Breakout",
            close,
            float(last["High"]),
            note=f"Gap-up day prior; 5m break below yesterday low ₹{y_low:.2f}.",
            timeframe="5 Min",
        )
    return None


def screener_momentum_930(symbol: str) -> Signal | None:
    """5-TF momentum (Investing.com style) — entry 9:30–9:45 AM."""
    if not is_momentum_entry_window():
        return None

    mtf = analyze_multi_timeframe(symbol)
    if not mtf or mtf["consensus"] == "mixed":
        return None

    session = today_session_df(fetch_intraday(symbol, "15m"), now_ist().date())
    candles = first_two_15m_candles(session)
    if candles is None:
        return None

    first, second = candles
    entry = float(second["Open"])
    note = f"{format_breakdown(mtf['breakdown'])}. {STANDARD_EXIT}"

    if mtf["consensus"] == "strong_buy":
        return entry_long(
            symbol,
            "Screener Momentum (5 TF)",
            entry,
            float(first["Low"]),
            rr1=1.0,
            rr2=1.5,
            best_rr=1.0,
            note=note,
            timeframe="15 Min + MTF",
            trailing_note="Book 50% at T1 (1:1). Trail remainder; exit by 3:30 PM IST.",
        )

    if mtf["consensus"] == "strong_sell":
        return entry_short(
            symbol,
            "Screener Momentum (5 TF)",
            entry,
            float(first["High"]),
            rr1=1.0,
            rr2=1.5,
            best_rr=1.0,
            note=note,
            timeframe="15 Min + MTF",
            trailing_note="Book 50% at T1 (1:1). Trail remainder; exit by 3:30 PM IST.",
        )
    return None


def consolidation_breakout_3m(symbol: str) -> Signal | None:
    """Sector SuperTrend + 4H consolidation + 3m breakout (9:18–9:36 AM)."""
    if not is_consolidation_entry_window():
        return None

    sectors = strong_sectors()
    if not is_consolidation_candidate(symbol, sectors):
        return None

    lv = consolidation_levels(symbol)
    if not lv:
        return None

    data = check_3m_breakout_entry(symbol, lv)
    if not data:
        return None

    mark_consolidation_active(symbol)
    sig = entry_long(
        symbol,
        "Consolidation Breakout (3m)",
        data["entry"],
        data["stop_loss"],
        rr1=1.0,
        rr2=2.0,
        best_rr=2.0,
        note=(
            f"Sector {sector_for(symbol)} | 4H R ₹{data['resistance']:.2f} "
            f"S ₹{data['support']:.2f} | 3m broke 1st candle high ₹{data['first_3m_high']:.2f}."
        ),
        timeframe="3 Min / 4H",
        trailing_note="Trail: exit 3m close below 9 EMA. " + STANDARD_EXIT,
    )
    return sig


def consolidation_trail_exit_9ema(symbol: str) -> Signal | None:
    """Book profits when 3m closes below 9 EMA after consolidation entry."""
    if not is_market_open() or not is_consolidation_active(symbol):
        return None

    data = check_9ema_trail_exit(symbol)
    if not data:
        return None

    clear_consolidation_active(symbol)
    return exit_signal(
        symbol,
        "Consolidation Trail Exit (9 EMA)",
        data["exit_price"],
        f"3m closed below 9 EMA (₹{data['ema9']:.2f}). Book remaining profits now.",
        side="SELL",
    )


STRATEGY_SCANNERS = [
    winning_combination,
    orb_15min,
    gap_day_breakout,
    screener_momentum_930,
    consolidation_breakout_3m,
    consolidation_trail_exit_9ema,
]

STRATEGY_NAMES = [fn.__name__ for fn in STRATEGY_SCANNERS]
