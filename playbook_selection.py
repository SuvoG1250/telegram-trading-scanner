"""
Module 1 — Stock selection (yfinance / NSE proxy).

Automated here:
  • B — Trend / momentum from prior session % change (NSE top movers proxy)
  • C — Top 10 heavy Nifty names (reversal / liquidity basket)
  • D — Volatility basket (large % move names for scalp-style watch)

Manual / external (not in code): A — Dhan ScanX; E — ClearTrend (paid).
"""

from __future__ import annotations

import logging

from config import (
    VOLATILE_SCAN_MAX_PCT,
    VOLATILE_SCAN_MIN_PCT,
    WATCHLIST_MAX,
)
from data_fetcher import fetch_daily
from sector_map import sector_for
from stocks import get_scan_universe

logger = logging.getLogger(__name__)

# Method C — heaviest Nifty large-caps (static top 10)
TOP_NIFTY_10: list[str] = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "BHARTIARTL",
    "ITC",
    "LT",
    "SBIN",
    "KOTAKBANK",
]


def _prior_day_pct_change(symbol: str) -> float | None:
    d = fetch_daily(symbol, period="10d")
    if d is None or len(d) < 3:
        return None
    last = float(d.iloc[-1]["Close"])
    prev = float(d.iloc[-2]["Close"])
    if prev <= 0:
        return None
    return (last / prev - 1.0) * 100.0


def build_playbook_watchlist() -> tuple[list[str], list[dict]]:
    """
    Merge Method B + C + D into one deduped watchlist (capped).
    """
    by_symbol: dict[str, dict] = {}

    def touch(sym: str, tag: str) -> None:
        ch = _prior_day_pct_change(sym)
        if ch is None:
            return
        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol": sym,
                "sector": sector_for(sym),
                "pct_change": round(ch, 2),
                "sources": [],
                "rank_score": abs(ch),
                "mtf_consensus": "mixed",
                "mtf_score": 0,
                "consolidation": False,
            }
        if tag not in by_symbol[sym]["sources"]:
            by_symbol[sym]["sources"].append(tag)
        by_symbol[sym]["rank_score"] = max(by_symbol[sym]["rank_score"], abs(ch))

    for sym in TOP_NIFTY_10:
        touch(sym, "C:Top10 Nifty")

    movers: list[tuple[str, float]] = []
    for sym in get_scan_universe():
        ch = _prior_day_pct_change(sym)
        if ch is None:
            continue
        movers.append((sym, ch))
    movers.sort(key=lambda x: abs(x[1]), reverse=True)

    for sym, ch in movers[:35]:
        touch(sym, "B:NSE trend proxy")
        ap = abs(ch)
        if VOLATILE_SCAN_MIN_PCT <= ap <= VOLATILE_SCAN_MAX_PCT + 2.0:
            touch(sym, "D:Volatility")

    ranked = sorted(by_symbol.values(), key=lambda x: x["rank_score"], reverse=True)
    top = ranked[:WATCHLIST_MAX]
    selected = [r["symbol"] for r in top]
    logger.info(
        "Playbook watchlist (%s names): %s",
        len(selected),
        ", ".join(selected[:25]) + ("..." if len(selected) > 25 else ""),
    )
    return selected, top
