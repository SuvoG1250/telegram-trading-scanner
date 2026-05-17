"""Part 1: Pre-market stock selection."""

from __future__ import annotations

import logging

import pandas as pd

from config import (
    NEAR_52W_HIGH_PCT,
    VOLUME_MULTIPLIER,
    WATCHLIST_MAX,
    WATCHLIST_MIN,
)
from data_fetcher import fetch_daily
from market_time import now_ist
from momentum_screener import analyze_multi_timeframe
from consolidation import is_consolidation_candidate, strong_sectors
from market_sentiment import format_sentiment_block
from stocks import NIFTY_100_SYMBOLS

logger = logging.getLogger(__name__)


def _pct_from_high(current: float, high_52w: float) -> float:
    if high_52w <= 0:
        return 100.0
    return ((high_52w - current) / high_52w) * 100.0


def score_stock(symbol: str, sectors: set[str] | None = None) -> dict | None:
    daily = fetch_daily(symbol, period="1y")
    if daily.empty or len(daily) < 25:
        return None

    last = daily.iloc[-1]
    prev = daily.iloc[-2] if len(daily) > 1 else last
    window = daily.tail(22)
    avg_vol_1m = float(window["Volume"].mean())
    if avg_vol_1m <= 0:
        return None

    high_52w = float(daily["High"].tail(252).max())
    current_price = float(last["Close"])
    dist_pct = _pct_from_high(current_price, high_52w)
    if dist_pct > NEAR_52W_HIGH_PCT:
        return None

    today_vol = float(last["Volume"])
    # Early session: volume may still build; use max(today, partial) vs average
    vol_ratio = today_vol / avg_vol_1m
    if vol_ratio < VOLUME_MULTIPLIER:
        return None

    momentum = float(last["Close"]) / float(prev["Close"]) - 1.0

    mtf = analyze_multi_timeframe(symbol)
    mtf_score = int(mtf["mtf_score"]) if mtf else 0
    consensus = mtf["consensus"] if mtf else "mixed"

    sectors = sectors if sectors is not None else strong_sectors()
    cons = is_consolidation_candidate(symbol, sectors)
    cons_bonus = 2 if cons else 0

    return {
        "symbol": symbol,
        "price": current_price,
        "dist_from_52w_high_pct": dist_pct,
        "volume_ratio": vol_ratio,
        "momentum": momentum,
        "mtf_score": mtf_score,
        "mtf_consensus": consensus,
        "consolidation": bool(cons),
        "rank_score": mtf_score + cons_bonus,
    }


def build_watchlist(symbols: list[str] | None = None) -> tuple[list[str], list[dict]]:
    universe = symbols or NIFTY_100_SYMBOLS
    sectors = strong_sectors()
    candidates: list[dict] = []
    for sym in universe:
        try:
            row = score_stock(sym, sectors)
            if row:
                candidates.append(row)
        except Exception:
            logger.exception("Premarket scoring failed for %s", sym)

    if not candidates:
        logger.warning("No stocks passed pre-market filters.")
        return [], []

    ranked = sorted(
        candidates,
        key=lambda x: (
            x["rank_score"],
            x["volume_ratio"],
            -x["dist_from_52w_high_pct"],
            x["momentum"],
        ),
        reverse=True,
    )
    count = min(WATCHLIST_MAX, max(WATCHLIST_MIN, len(ranked)))
    count = min(count, len(ranked))
    top = ranked[:count]
    selected = [r["symbol"] for r in top]
    logger.info(
        "Premarket watchlist (%s IST): %s",
        now_ist().strftime("%H:%M"),
        ", ".join(selected),
    )
    return selected, top


def format_watchlist_message(rows: list[dict]) -> str:
    lines = [
        f"📋 Pre-Market Watchlist ({now_ist().strftime('%d %b %Y %H:%M IST')})",
        format_sentiment_block(),
        "",
        "_Universe: Nifty 100 large-cap | Quality + sector SuperTrend + consolidation_\n",
    ]
    for row in rows:
        tag = row.get("mtf_consensus", "mixed")
        if tag == "strong_buy":
            label = "🟢 Strong Buying"
        elif tag == "strong_sell":
            label = "🔴 Strong Selling"
        else:
            label = "⚪ Mixed"
        cons = " 📐4H consolidation" if row.get("consolidation") else ""
        lines.append(f"• {row['symbol']} — {label} ({row['mtf_score']}/5){cons}")
    return "\n".join(lines)
