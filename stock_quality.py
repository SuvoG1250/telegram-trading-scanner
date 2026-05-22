"""Liquidity, volatility, and penny-stock filters."""

from __future__ import annotations

import pandas as pd

from config import MAX_STOCK_PRICE, MIN_ATR_PCT, MIN_AVG_VOLUME, MIN_STOCK_PRICE
from data_fetcher import fetch_daily


def passes_quality_filter(symbol: str) -> tuple[bool, dict]:
    daily = fetch_daily(symbol, period="6mo")
    if daily.empty or len(daily) < 22:
        return False, {}

    last = daily.iloc[-1]
    price = float(last["Close"])
    if price < MIN_STOCK_PRICE:
        return False, {"reason": "below min price"}
    if price > MAX_STOCK_PRICE:
        return False, {"reason": "above max price"}

    avg_vol = float(daily["Volume"].tail(22).mean())
    if avg_vol < MIN_AVG_VOLUME:
        return False, {"reason": "low liquidity"}

    hl = daily.tail(14)
    atr_pct = float(((hl["High"] - hl["Low"]) / hl["Close"]).mean() * 100)
    if atr_pct < MIN_ATR_PCT:
        return False, {"reason": "low volatility"}

    return True, {
        "price": price,
        "avg_volume": int(avg_vol),
        "atr_pct": round(atr_pct, 2),
    }
