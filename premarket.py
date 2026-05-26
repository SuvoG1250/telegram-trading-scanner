"""Part 1: Pre-market stock selection — all sectors, Nifty 100, long-term picks."""

from __future__ import annotations

import logging

from config import (
    MIN_STOCK_MOVE_POTENTIAL_PCT,
    SCAN_FULL_UNIVERSE,
    SEND_LONG_TERM_PICKS_DAILY,
    WATCHLIST_MAX,
)
from data_fetcher import fetch_daily
from long_term_picks import build_long_term_picks, format_long_term_message
from market_sentiment import format_sentiment_block
from market_time import now_ist
from playbook_selection import build_playbook_watchlist
from sector_map import group_rows_by_sector, sector_for
from sector_overview import format_sector_overview_block
from state import long_term_picks_sent, mark_long_term_picks_sent
from config import USE_TRADE_FILTERS
from stocks import get_scan_universe, get_tradeable_universe
from trade_filters import filter_symbols

logger = logging.getLogger(__name__)


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
                "sources": ["Fallback"],
                "pct_change": 0.0,
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
    """Module 1 — merged B (trend proxy) + C (Top 10 Nifty) + D (volatility)."""
    if symbols:
        rows = []
        for sym in symbols:
            rows.append(
                {
                    "symbol": sym,
                    "sector": sector_for(sym),
                    "sources": ["Custom"],
                    "pct_change": 0.0,
                    "rank_score": 0.0,
                    "mtf_consensus": "mixed",
                    "mtf_score": 0,
                    "consolidation": False,
                }
            )
        return list(symbols), rows

    try:
        selected, ranked = build_playbook_watchlist()
        from config import SCAN_ALL_UNIVERSE_INTRADAY

        if SCAN_ALL_UNIVERSE_INTRADAY:
            universe = get_tradeable_universe() if USE_TRADE_FILTERS else get_scan_universe()
            if USE_TRADE_FILTERS:
                universe = filter_symbols(universe)
            from config import MAX_STOCK_PRICE, MIN_STOCK_PRICE, SCAN_UNIVERSE_MODE

            logger.info(
                "Intraday scan: %s symbols | mode=%s Rs %.0f-%.0f | filters=%s | playbook=%s",
                len(universe),
                SCAN_UNIVERSE_MODE,
                MIN_STOCK_PRICE,
                MAX_STOCK_PRICE,
                USE_TRADE_FILTERS,
                len(selected),
            )
            return universe, ranked
        return selected, ranked
    except Exception:
        logger.exception("Playbook watchlist failed; using fallback.")
        return _fallback_watchlist()


def format_watchlist_message(rows: list[dict]) -> str:
    lines = [
        f"📋 <b>Master Intraday Playbook</b> ({now_ist().strftime('%d %b %Y %H:%M IST')})",
        f"<i>Playbook highlights below. Intraday scan: <b>F&amp;O + MIS + ~{MIN_STOCK_MOVE_POTENTIAL_PCT:.0f}% move potential</b> "
        f"(when filters on). A: Dhan ScanX &amp; E: ClearTrend = manual.</i>",
        "",
        "<b>Module 1 — Selection tags</b>",
    ]
    for row in rows[:25]:
        src = ", ".join(row.get("sources") or [])
        ch = row.get("pct_change", 0)
        lines.append(f"• <b>{row['symbol']}</b> ({row.get('sector', '')}) {ch:+.2f}% — <i>{src}</i>")
    if len(rows) > 25:
        lines.append(f"<i>…+{len(rows) - 25} more in scan list</i>")
    lines.extend(["", format_sentiment_block(), "", format_sector_overview_block(), "", "<b>By sector</b>"])
    by_sec = group_rows_by_sector(rows)
    for sector in sorted(by_sec.keys()):
        sec_rows = by_sec[sector][:6]
        if not sec_rows:
            continue
        tickers = ", ".join(r["symbol"] for r in sec_rows)
        extra = len(by_sec[sector]) - len(sec_rows)
        suffix = f" +{extra}" if extra > 0 else ""
        lines.append(f"• <b>{sector}</b>: {tickers}{suffix}")

    lines.extend(
        [
            "",
            "<b>Module 2 — Execution</b>",
            "• <b>Setup 1</b>: 1m morning 9:16–10:30 (pennant break proxy)",
            "• <b>Setup 2</b>: 5m/15m price action from 10:30 (no overlap with Setup 1)",
            "• <b>EMA20+ST</b>: Below EMA20, Supertrend red, SELL on low break (5m)",
            "• <b>EMA 9/15 & 9/21</b>: Crossover on 15m + volume",
            "",
            "<b>Module 3 — Risk</b>: max SL 0.6% | min 1:2 R:R | alerts only F&amp;O + MIS + high-range names.",
        ]
    )

    if SEND_LONG_TERM_PICKS_DAILY and not long_term_picks_sent():
        try:
            picks = build_long_term_picks()
            lines.extend(["", format_long_term_message(picks)])
            mark_long_term_picks_sent()
        except Exception:
            logger.exception("Long-term picks failed")

    return "\n".join(lines)
