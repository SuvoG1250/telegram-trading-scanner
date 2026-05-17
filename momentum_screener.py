"""
Multi-timeframe momentum screener (Investing.com Technicals style).

Uses free yfinance data across 5 timeframes: 15m, 1h, daily, weekly, monthly.
Maps each to Strong Buying / Strong Selling / Neutral, similar to screener momentum tabs.
"""

from __future__ import annotations

import logging
from typing import Literal

import pandas as pd

from config import MOMENTUM_MIN_TIMEFRAMES
from data_fetcher import fetch_history, fetch_intraday, today_session_df
from indicators import ema, rsi
from market_time import now_ist

logger = logging.getLogger(__name__)

Bias = Literal["strong_buy", "strong_sell", "neutral"]
TFLabel = Literal["15m", "1h", "daily", "weekly", "monthly"]

TIMEFRAME_SPECS: list[tuple[TFLabel, str, bool]] = [
    ("15m", "15m", True),
    ("1h", "1h", False),
    ("daily", "1d", False),
    ("weekly", "1wk", False),
    ("monthly", "1mo", False),
]


def _bias_from_ohlc(df: pd.DataFrame) -> Bias:
    if df is None or len(df) < 25:
        return "neutral"
    close = df["Close"]
    e5 = ema(close, 5)
    e20 = ema(close, 20)
    r = rsi(close, 14)
    c, e5v, e20v, rv = float(close.iloc[-1]), float(e5.iloc[-1]), float(e20.iloc[-1]), float(r.iloc[-1])

    if rv >= 55 and c > e20v and e5v > e20v:
        return "strong_buy"
    if rv <= 45 and c < e20v and e5v < e20v:
        return "strong_sell"
    return "neutral"


def _fetch_tf(symbol: str, interval: str, intraday: bool) -> pd.DataFrame:
    if intraday:
        df = fetch_intraday(symbol, interval)  # type: ignore[arg-type]
        return today_session_df(df, now_ist().date()) if not df.empty else df
    return fetch_history(symbol, interval)  # type: ignore[arg-type]


def analyze_multi_timeframe(symbol: str) -> dict | None:
    """
    Return momentum breakdown across 5 timeframes.
    Inspired by Investing.com Stock Screener > Technicals (India).
    """
    breakdown: dict[str, str] = {}
    buy_count = sell_count = 0

    for label, interval, intraday in TIMEFRAME_SPECS:
        try:
            df = _fetch_tf(symbol, interval, intraday)
            bias = _bias_from_ohlc(df)
        except Exception:
            logger.exception("MTF fetch failed %s %s", symbol, label)
            bias = "neutral"

        if bias == "strong_buy":
            buy_count += 1
            breakdown[label] = "Strong Buying"
        elif bias == "strong_sell":
            sell_count += 1
            breakdown[label] = "Strong Selling"
        else:
            breakdown[label] = "Neutral"

    if buy_count == 0 and sell_count == 0:
        return None

    if buy_count >= MOMENTUM_MIN_TIMEFRAMES:
        consensus = "strong_buy"
    elif sell_count >= MOMENTUM_MIN_TIMEFRAMES:
        consensus = "strong_sell"
    else:
        consensus = "mixed"

    return {
        "symbol": symbol,
        "consensus": consensus,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "breakdown": breakdown,
        "mtf_score": max(buy_count, sell_count),
    }


def format_breakdown(breakdown: dict[str, str]) -> str:
    order: list[TFLabel] = ["15m", "1h", "daily", "weekly", "monthly"]
    return " | ".join(f"{k}: {breakdown.get(k, '—')}" for k in order)


def first_two_15m_candles(session_15m: pd.DataFrame) -> tuple[pd.Series, pd.Series] | None:
    """Candle 1: 9:15–9:30, Candle 2: 9:30–9:45 (NSE)."""
    day = session_15m.between_time("09:15", "09:45")
    if len(day) < 2:
        day = session_15m.head(2)
    if len(day) < 2:
        return None
    return day.iloc[0], day.iloc[1]
