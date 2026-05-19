"""
Trade eligibility filters — fewer alerts, higher quality.

Equity: F&O underlying + MIS proxy (F&O + liquid) + min intraday move potential.
Options: min premium target % (separate from cash playbook caps).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from config import (
    APPLY_QUALITY_FILTER,
    MIN_EQUITY_TARGET_PROFIT_PCT,
    MIN_STOCK_MOVE_POTENTIAL_PCT,
    REQUIRE_FNO_ELIGIBLE,
    REQUIRE_INTRADAY_MARGIN,
)
from stock_quality import passes_quality_filter
from stocks import is_fno_eligible

logger = logging.getLogger(__name__)


@lru_cache(maxsize=512)
def _avg_daily_range_pct(symbol: str) -> float | None:
    from data_fetcher import fetch_daily

    daily = fetch_daily(symbol, period="3mo")
    if daily.empty or len(daily) < 12:
        return None
    tail = daily.tail(10)
    ranges = (tail["High"] - tail["Low"]) / tail["Close"].replace(0, float("nan"))
    val = float(ranges.mean() * 100)
    if val != val:  # NaN
        return None
    return round(val, 2)


def has_move_potential(symbol: str, min_pct: float | None = None) -> bool:
    """Stock often trades with enough range to target ~5% intraday moves."""
    threshold = min_pct if min_pct is not None else MIN_STOCK_MOVE_POTENTIAL_PCT
    ok, metrics = passes_quality_filter(symbol)
    if ok:
        atr_pct = float(metrics.get("atr_pct", 0))
        if atr_pct >= threshold * 0.45:
            return True
    adr = _avg_daily_range_pct(symbol)
    if adr is not None and adr >= threshold * 0.75:
        return True
    return False


def passes_intraday_margin_proxy(symbol: str) -> bool:
    """
  F&O names with normal liquidity typically have MIS intraday margin on NSE brokers.
  True margin API can be added later (Upstox/Dhan).
  """
    if not is_fno_eligible(symbol):
        return False
    if not APPLY_QUALITY_FILTER:
        return True
    ok, _ = passes_quality_filter(symbol)
    return ok


def passes_trade_filters(symbol: str) -> tuple[bool, str]:
    if REQUIRE_FNO_ELIGIBLE and not is_fno_eligible(symbol):
        return False, "not F&O eligible"
    if REQUIRE_INTRADAY_MARGIN and not passes_intraday_margin_proxy(symbol):
        return False, "no intraday margin proxy (F&O + liquidity)"
    if not has_move_potential(symbol):
        return False, f"move potential < {MIN_STOCK_MOVE_POTENTIAL_PCT}%"
    return True, "ok"


def filter_symbols(symbols: list[str]) -> list[str]:
    out: list[str] = []
    skipped = 0
    for sym in symbols:
        ok, reason = passes_trade_filters(sym)
        if ok:
            out.append(sym)
        else:
            skipped += 1
            logger.debug("Filter skip %s: %s", sym, reason)
    logger.info(
        "Trade filters: %s / %s symbols pass (F&O=%s MIS=%s min move %.1f%%)",
        len(out),
        len(symbols),
        REQUIRE_FNO_ELIGIBLE,
        REQUIRE_INTRADAY_MARGIN,
        MIN_STOCK_MOVE_POTENTIAL_PCT,
    )
    return out


def min_equity_target_profit_pct() -> float:
    """Playbook caps SL at 0.6%% — best target is ~1.2%% at 1:2 R:R."""
    return MIN_EQUITY_TARGET_PROFIT_PCT
