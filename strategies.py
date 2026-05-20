"""
Master Intraday Playbook — Module 2 execution only (yfinance).

Setup 1 — 1-minute morning breakout (9:16–10:30 IST).
Setup 2 — Core price action on 5m + 15m (from 10:30 IST; no overlap with Setup 1 window).

Module 1 stock selection lives in playbook_selection.py.
Module 3 risk (0.6% max SL, 1:2 RR, 70/30 + 10 EMA trail note) is enforced in risk.levels_playbook.

External tools (Dhan ScanX, NSE website manual, ClearTrend) are not called here — add picks manually if needed.
"""

from __future__ import annotations

import logging

import pandas as pd

from chaitu50c import ChaituParams, replay_last_bar_signal
from config import (
    CHAITU_ENHANCED_MODE,
    CHAITU_INTERVAL,
    EMA_FAST,
    EMA_INTERVAL,
    EMA_SLOW,
    EMA21_FAST,
    EMA21_SLOW,
    EMA_VOLUME_MULTIPLIER,
    RISK_PER_TRADE_INR,
    SCAN_STRATEGIES,
    SIGNALS_ONLY_TELEGRAM,
)
from ema_crossover import (
    add_emas,
    crossover_signal,
    multi_day_volume_spike,
    position_quantity,
)
from data_fetcher import Interval, get_today_session
from market_time import (
    is_chaitu_session,
    is_core_price_action_window,
    is_market_open,
    is_morning_1m_playbook_window,
    now_ist,
)
from signal_builder import ema_entry_long, ema_entry_short, playbook_entry_long, playbook_entry_short
from telegram_client import Signal

logger = logging.getLogger(__name__)

STANDARD_EXIT = "Square off ALL intraday positions by 3:30 PM IST."


def _session_1m(symbol: str) -> pd.DataFrame:
    return get_today_session(symbol, "1m")


def _session_5m(symbol: str) -> pd.DataFrame:
    return get_today_session(symbol, "5m")


def _session_15m(symbol: str) -> pd.DataFrame:
    return get_today_session(symbol, "15m")


def setup_1_morning_1m_breakout(symbol: str) -> Signal | None:
    """
    Setup 1 — 1m morning window 9:16–10:30.
    Proxy: tight range (pennant) over last N bars, close breaks boundary.
    """
    if not is_market_open() or not is_morning_1m_playbook_window():
        return None

    session = _session_1m(symbol)
    if len(session) < 12:
        return None

    recent = session.tail(18).iloc[:-1]
    box_hi = float(recent["High"].max())
    box_lo = float(recent["Low"].min())
    box_range_pct = (box_hi - box_lo) / box_lo * 100 if box_lo > 0 else 100
    if box_range_pct > 1.2:
        return None

    last = session.iloc[-1]
    close = float(last["Close"])
    o = float(last["Open"])
    body = abs(close - o)
    if body < (box_hi - box_lo) * 0.15:
        return None

    if close > box_hi and close > o:
        note = (
            f"Setup 1 | 1m close above micro-range high ₹{box_hi:.2f} (9:16–10:30). "
            f"Pennant proxy; confirm on tape. {STANDARD_EXIT}"
        )
        return playbook_entry_long(
            symbol,
            "Setup 1: 1-Min Morning Breakout",
            close,
            min(box_lo, close * 0.997),
            note=note,
            timeframe="1 Min",
        )

    if close < box_lo and close < o:
        note = (
            f"Setup 1 | 1m close below micro-range low ₹{box_lo:.2f} (9:16–10:30). "
            f"Pennant proxy. {STANDARD_EXIT}"
        )
        return playbook_entry_short(
            symbol,
            "Setup 1: 1-Min Morning Breakout",
            close,
            max(box_hi, close * 1.003),
            note=note,
            timeframe="1 Min",
        )
    return None


def setup_2_core_price_action_5m_15m(symbol: str) -> Signal | None:
    """
    Setup 2 — 5m range + 15m confirmation (after 10:30 only).
    """
    if not is_market_open() or not is_core_price_action_window():
        return None

    s5 = _session_5m(symbol)
    s15 = _session_15m(symbol)
    if len(s5) < 10 or len(s15) < 2:
        return None

    window = s5.tail(8)
    rh = float(window["High"].max())
    rl = float(window["Low"].min())
    if rh <= rl or (rh - rl) / rl * 100 > 2.5:
        return None

    last5 = s5.iloc[-1]
    c5 = float(last5["Close"])
    last15 = s15.iloc[-1]
    c15 = float(last15["Close"])

    if c5 > rh and c15 >= c5 * 0.998:
        note = (
            f"Setup 2 | 5m structural break above ₹{rh:.2f} with 15m alignment. "
            f"Base-and-breakout proxy. {STANDARD_EXIT}"
        )
        return playbook_entry_long(
            symbol,
            "Setup 2: Core Price Action (5m/15m)",
            c5,
            min(rl, c5 * 0.998),
            note=note,
            timeframe="5m / 15m",
        )

    if c5 < rl and c15 <= c5 * 1.002:
        note = (
            f"Setup 2 | 5m structural break below ₹{rl:.2f} with 15m alignment. "
            f"{STANDARD_EXIT}"
        )
        return playbook_entry_short(
            symbol,
            "Setup 2: Core Price Action (5m/15m)",
            c5,
            max(rh, c5 * 1.002),
            note=note,
            timeframe="5m / 15m",
        )
    return None


def _session_interval(symbol: str, interval: Interval) -> pd.DataFrame:
    return get_today_session(symbol, interval)


def setup_3_chaitu50c_breakout(symbol: str) -> Signal | None:
    """
    Setup 3 — Pine port "Intraday BUY/SELL & AUTO SL by chaitu50c".
    Single/double candle color flips + close beyond prior opposite candle extreme.
    Session 9:15–15:25 IST; enhanced mode: zone suppression + SL→opposite + slModeOnly.
    """
    if not is_market_open() or not is_chaitu_session():
        return None

    interval: Interval = CHAITU_INTERVAL if CHAITU_INTERVAL in ("1m", "3m", "5m", "15m") else "5m"
    session = _session_interval(symbol, interval)
    if len(session) < 5:
        return None

    params = ChaituParams(enhanced_mode=CHAITU_ENHANCED_MODE)
    fire = replay_last_bar_signal(session, params)
    if fire is None:
        return None

    tf_label = interval.upper().replace("M", " Min")
    double_tag = " (double-candle)" if fire.double_candle else ""
    exit_note = STANDARD_EXIT

    note = "" if SIGNALS_ONLY_TELEGRAM else (
        f"Chaitu50c{double_tag} · SL ₹{fire.stop_level:.2f}. {exit_note}"
    )
    name = "Chaitu50c"

    if fire.side == "BUY":
        return playbook_entry_long(
            symbol,
            name,
            fire.entry,
            fire.stop_level,
            note=note,
            timeframe=tf_label,
        )

    return playbook_entry_short(
        symbol,
        name,
        fire.entry,
        fire.stop_level,
        note=note,
        timeframe=tf_label,
    )


def _ema_crossover_setup(
    symbol: str,
    *,
    fast: int,
    slow: int,
    strategy_name: str,
) -> Signal | None:
    """9/x EMA crossover on 5m with volume momentum. SL: prior bar low/high."""
    if not is_market_open() or not is_chaitu_session():
        return None

    interval: Interval = EMA_INTERVAL if EMA_INTERVAL in ("1m", "3m", "5m", "15m") else "5m"
    session = _session_interval(symbol, interval)
    if len(session) < max(slow, 20) + 2:
        return None

    if not multi_day_volume_spike(symbol):
        return None

    df = add_emas(session, fast=fast, slow=slow)
    side = crossover_signal(df)
    if side is None:
        return None

    prev = df.iloc[-2]
    cur = df.iloc[-1]
    entry = float(cur["Close"])
    tf = interval.upper().replace("M", " Min")
    note = "" if SIGNALS_ONLY_TELEGRAM else (
        f"{strategy_name} · Volume > {EMA_VOLUME_MULTIPLIER}x avg · {STANDARD_EXIT}"
    )

    if side == "BUY":
        sl = float(prev["Low"])
        qty = position_quantity(entry, sl, RISK_PER_TRADE_INR)
        return ema_entry_long(symbol, strategy_name, entry, sl, note=note, timeframe=tf, suggested_qty=qty)

    sl = float(prev["High"])
    qty = position_quantity(entry, sl, RISK_PER_TRADE_INR)
    return ema_entry_short(symbol, strategy_name, entry, sl, note=note, timeframe=tf, suggested_qty=qty)


def setup_4_ema_crossover(symbol: str) -> Signal | None:
    """Setup 4 — 9 EMA crosses 15 EMA on 5m."""
    return _ema_crossover_setup(
        symbol,
        fast=EMA_FAST,
        slow=EMA_SLOW,
        strategy_name="EMA 9/15 Crossover",
    )


def setup_5_ema_9_21_crossover(symbol: str) -> Signal | None:
    """Setup 5 — 9 EMA crosses 21 EMA on 5m."""
    return _ema_crossover_setup(
        symbol,
        fast=EMA21_FAST,
        slow=EMA21_SLOW,
        strategy_name="EMA 9/21 Crossover",
    )


_ALL_SCANNERS = [
    setup_1_morning_1m_breakout,
    setup_2_core_price_action_5m_15m,
    setup_3_chaitu50c_breakout,
    setup_4_ema_crossover,
    setup_5_ema_9_21_crossover,
]
_CHAITU_ONLY = [setup_3_chaitu50c_breakout]
_EMA_915 = [setup_4_ema_crossover]
_EMA_921 = [setup_5_ema_9_21_crossover]
_EMA_ALL = _EMA_915 + _EMA_921

if SCAN_STRATEGIES == "all":
    STRATEGY_SCANNERS = _ALL_SCANNERS
elif SCAN_STRATEGIES == "both":
    STRATEGY_SCANNERS = _CHAITU_ONLY + _EMA_ALL
elif SCAN_STRATEGIES == "ema":
    STRATEGY_SCANNERS = _EMA_ALL
elif SCAN_STRATEGIES in ("ema15", "ema_15"):
    STRATEGY_SCANNERS = _EMA_915
elif SCAN_STRATEGIES in ("ema21", "ema_21"):
    STRATEGY_SCANNERS = _EMA_921
else:
    STRATEGY_SCANNERS = _CHAITU_ONLY

STRATEGY_NAMES = [fn.__name__ for fn in STRATEGY_SCANNERS]
