"""Part 1: Pre-market stock selection."""

from __future__ import annotations

import logging

import pandas as pd

from config import (
    NEAR_52W_HIGH_PCT,
    SCAN_FULL_UNIVERSE,
    VOLUME_MULTIPLIER_EARLY,
    VOLUME_MULTIPLIER_STRICT,
    WATCHLIST_MAX,
    WATCHLIST_MIN,
)
from data_fetcher import fetch_daily
from market_time import now_ist
from momentum_screener import analyze_multi_timeframe
from market_sentiment import format_sentiment_block
from market_time import is_premarket_window, ist_time_tuple
from stocks import NIFTY_50_SYMBOLS

logger = logging.getLogger(__name__)


def _pct_from_high(current: float, high_52w: float) -> float:
    if high_52w <= 0:
        return 100.0
    return ((high_52w - current) / high_52w) * 100.0


def score_stock(symbol: str) -> dict | None:
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
    prev_vol = float(prev["Volume"])
    ref_vol = max(today_vol, prev_vol)
    vol_ratio = ref_vol / avg_vol_1m

    t = ist_time_tuple()
    vol_min = VOLUME_MULTIPLIER_EARLY if is_premarket_window() or t < (10, 0) else VOLUME_MULTIPLIER_STRICT
    if vol_ratio < vol_min:
        return None

    momentum = float(last["Close"]) / float(prev["Close"]) - 1.0

    mtf = analyze_multi_timeframe(symbol)
    mtf_score = int(mtf["mtf_score"]) if mtf else 0
    consensus = mtf["consensus"] if mtf else "mixed"

    cons_bonus = 0

    return {
        "symbol": symbol,
        "price": current_price,
        "dist_from_52w_high_pct": dist_pct,
        "volume_ratio": vol_ratio,
        "momentum": momentum,
        "mtf_score": mtf_score,
        "mtf_consensus": consensus,
        "consolidation": False,
        "rank_score": mtf_score + cons_bonus,
    }


def _fallback_watchlist() -> tuple[list[str], list[dict]]:
    """If filters are too strict early morning, use top Nifty 50 by momentum."""
    rows: list[dict] = []
    for sym in NIFTY_50_SYMBOLS:
        daily = fetch_daily(sym, period="3mo")
        if len(daily) < 5:
            continue
        last, prev = daily.iloc[-1], daily.iloc[-2]
        rows.append(
            {
                "symbol": sym,
                "mtf_score": 0,
                "mtf_consensus": "mixed",
                "rank_score": float(last["Close"]) / float(prev["Close"]) - 1,
                "volume_ratio": 1.0,
                "consolidation": False,
            }
        )
    ranked = sorted(rows, key=lambda x: x["rank_score"], reverse=True)
    if not SCAN_FULL_UNIVERSE:
        ranked = ranked[:WATCHLIST_MAX]
    syms = [r["symbol"] for r in ranked]
    return syms, ranked


def build_watchlist(symbols: list[str] | None = None) -> tuple[list[str], list[dict]]:
    universe = symbols or NIFTY_50_SYMBOLS
    candidates: list[dict] = []
    for sym in universe:
        try:
            row = score_stock(sym)
            if row:
                candidates.append(row)
        except Exception:
            logger.exception("Premarket scoring failed for %s", sym)

    if not candidates:
        logger.warning("No stocks passed filters; using Nifty 50 fallback watchlist.")
        return _fallback_watchlist()

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
    if SCAN_FULL_UNIVERSE:
        top = ranked[:WATCHLIST_MAX]
    else:
        count = min(WATCHLIST_MAX, max(WATCHLIST_MIN, len(ranked)))
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
        f"📋 <b>Today's Stocks</b> ({now_ist().strftime('%d %b %Y %H:%M IST')})",
        "<i>Locked for the day — only these names are scanned. No new picks later.</i>",
        "",
        format_sentiment_block(),
        "",
        "_6 strategies per stock — signal when 2 of 6 agree → one alert._\n",
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
