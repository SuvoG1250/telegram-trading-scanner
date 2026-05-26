"""EMA(20) + Supertrend(10,3) bearish intraday — SELL on signal-candle low break."""

from __future__ import annotations

import logging

import pandas as pd

from config import EMA_VOLUME_MULTIPLIER
from data_fetcher import get_today_session
from ema_crossover import session_volume_spike
from indicators import compute_supertrend, ema
from market_time import is_ema20_st_entry_window, is_market_open
from signal_builder import entry_short

logger = logging.getLogger(__name__)

STRATEGY_NAME = "EMA20 + Supertrend Bearish"
ST_LENGTH = 10
ST_MULTIPLIER = 3.0
EMA_LENGTH = 20


def _st_flip_bearish(st: pd.DataFrame) -> bool:
    """Supertrend green → red on last bar (bullish -1 → bearish +1)."""
    if len(st) < 2:
        return False
    prev_dir = float(st["direction"].iloc[-2])
    curr_dir = float(st["direction"].iloc[-1])
    return prev_dir < 0 and curr_dir > 0


def scan_ema20_supertrend_bearish(symbol: str):
    if not is_market_open() or not is_ema20_st_entry_window():
        return None

    session = get_today_session(symbol, "5m")
    if len(session) < max(EMA_LENGTH, ST_LENGTH) + 5:
        return None

    df = session.copy()
    df["EMA20"] = ema(df["Close"], EMA_LENGTH)
    st = compute_supertrend(df, length=ST_LENGTH, multiplier=ST_MULTIPLIER)

    if not _st_flip_bearish(st):
        return None

    bar = df.iloc[-1]
    close = float(bar["Close"])
    ema20 = float(bar["EMA20"])
    if close >= ema20:
        return None

    if not session_volume_spike(df, multiplier=EMA_VOLUME_MULTIPLIER):
        return None

    sig_high = float(bar["High"])
    sig_low = float(bar["Low"])
    entry = round(sig_low * 0.9995, 2)
    stop_loss = round(sig_high * 1.0005, 2)
    if stop_loss <= entry:
        return None

    note = (
        f"Below EMA{EMA_LENGTH} · ST green→red · vol > {EMA_VOLUME_MULTIPLIER}x avg · "
        f"SELL below signal low Rs {sig_low:.2f} · SL above high Rs {sig_high:.2f}"
    )
    sig = entry_short(
        symbol,
        STRATEGY_NAME,
        entry,
        stop_loss,
        rr1=1.0,
        rr2=2.0,
        best_rr=2.0,
        note=note,
        timeframe="5 Min",
        trailing_note="Trail with Supertrend · 1:2 target · square off by 3:30 PM IST.",
    )
    if sig is None:
        logger.debug("EMA20+ST %s — risk/target validation rejected.", symbol)
    return sig
