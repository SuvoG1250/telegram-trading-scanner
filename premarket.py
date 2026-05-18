"""Part 1: Pre-market stock selection — all sectors, Nifty 100, long-term picks."""

from __future__ import annotations

import logging

from config import (
    NEAR_52W_HIGH_PCT,
    SCAN_FULL_UNIVERSE,
    SEND_LONG_TERM_PICKS_DAILY,
    VOLUME_MULTIPLIER_EARLY,
    VOLUME_MULTIPLIER_STRICT,
    WATCHLIST_MAX,
    WATCHLIST_MIN,
)
from data_fetcher import fetch_daily
from long_term_picks import build_long_term_picks, format_long_term_message
from market_sentiment import format_sentiment_block
from market_time import is_premarket_window, ist_time_tuple, now_ist
from momentum_screener import analyze_multi_timeframe
from sector_map import group_rows_by_sector, sector_for
from sector_overview import format_sector_overview_block
from state import long_term_picks_sent, mark_long_term_picks_sent
from stocks import NIFTY_50_SYMBOLS, get_scan_universe

logger = logging.getLogger(__name__)


def _pct_from_high(current: float, high_52w: float) -> float:
    if high_52w <= 0:
        return 100.0
    return ((high_52w - current) / high_52w) * 100.0


def score_stock_fast(symbol: str) -> dict | None:
    """Fast score for full-universe scan (no multi-TF)."""
    daily = fetch_daily(symbol, period="6mo")
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
    return {
        "symbol": symbol,
        "price": current_price,
        "dist_from_52w_high_pct": dist_pct,
        "volume_ratio": vol_ratio,
        "momentum": momentum,
        "mtf_score": 0,
        "mtf_consensus": "mixed",
        "consolidation": False,
        "rank_score": momentum * 10 + vol_ratio,
        "sector": sector_for(symbol),
    }


def score_stock(symbol: str, *, with_mtf: bool = True) -> dict | None:
    row = score_stock_fast(symbol)
    if row is None:
        return None
    if with_mtf:
        mtf = analyze_multi_timeframe(symbol)
        if mtf:
            row["mtf_score"] = int(mtf["mtf_score"])
            row["mtf_consensus"] = mtf["consensus"]
            row["rank_score"] = row["mtf_score"] + row["volume_ratio"]
    return row


def _fallback_watchlist() -> tuple[list[str], list[dict]]:
    rows: list[dict] = []
    for sym in get_scan_universe():
        daily = fetch_daily(sym, period="3mo")
        if len(daily) < 5:
            continue
        last, prev = daily.iloc[-1], daily.iloc[-2]
        rows.append(
            {
                "symbol": sym,
                "sector": sector_for(sym),
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
    universe = symbols or get_scan_universe()
    candidates: list[dict] = []
    for sym in universe:
        try:
            row = score_stock_fast(sym)
            if row:
                candidates.append(row)
        except Exception:
            logger.exception("Premarket scoring failed for %s", sym)

    if not candidates:
        logger.warning("No stocks passed filters; using fallback watchlist.")
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
        "Premarket watchlist (%s stocks, %s IST): %s",
        len(selected),
        now_ist().strftime("%H:%M"),
        ", ".join(selected[:20]) + ("..." if len(selected) > 20 else ""),
    )
    return selected, top


def format_watchlist_message(rows: list[dict]) -> str:
    lines = [
        f"📋 <b>Today's intraday scan</b> ({now_ist().strftime('%d %b %Y %H:%M IST')})",
        f"<i>Nifty 100 universe — <b>{len(rows)}</b> stocks locked for today. "
        f"2 of 6 strategies → signal.</i>",
        "",
        format_sentiment_block(),
        "",
        format_sector_overview_block(),
        "",
        "<b>Stocks by sector (top picks)</b>",
    ]
    by_sec = group_rows_by_sector(rows)
    for sector in sorted(by_sec.keys()):
        sec_rows = by_sec[sector][:5]
        if not sec_rows:
            continue
        tickers = ", ".join(r["symbol"] for r in sec_rows)
        extra = len(by_sec[sector]) - len(sec_rows)
        suffix = f" +{extra} more" if extra > 0 else ""
        lines.append(f"• <b>{sector}</b>: {tickers}{suffix}")

    lines.extend(["", "_Intraday: 6 strategies per stock — 2 of 6 must agree._"])

    if SEND_LONG_TERM_PICKS_DAILY and not long_term_picks_sent():
        try:
            picks = build_long_term_picks()
            lines.extend(["", format_long_term_message(picks)])
            mark_long_term_picks_sent()
        except Exception:
            logger.exception("Long-term picks failed")

    return "\n".join(lines)
