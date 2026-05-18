"""Sector health overview (daily SuperTrend breadth per sector)."""

from __future__ import annotations

import logging

from config import SECTOR_ST_MIN_BULLISH_PCT, SUPERTREND_LENGTH, SUPERTREND_MULTIPLIER
from data_fetcher import fetch_daily
from indicators import supertrend_direction
from sector_map import ALL_SECTORS, sector_for, symbols_by_sector

logger = logging.getLogger(__name__)


def _daily_st_bullish(symbol: str) -> bool:
    daily = fetch_daily(symbol, period="6mo")
    if len(daily) < 30:
        return False
    direction = supertrend_direction(
        daily, length=SUPERTREND_LENGTH, multiplier=SUPERTREND_MULTIPLIER
    )
    return float(direction.iloc[-1]) > 0


def sector_health() -> list[dict]:
    """Per-sector bullish % and label."""
    results: list[dict] = []
    groups = symbols_by_sector()
    for sector in ALL_SECTORS + ["Other"]:
        symbols = groups.get(sector, [])
        if not symbols:
            continue
        sample = symbols[:15]
        bullish = sum(1 for s in sample if _daily_st_bullish(s))
        checked = len(sample)
        pct = bullish / checked if checked else 0
        if pct >= SECTOR_ST_MIN_BULLISH_PCT:
            label, icon = "Strong", "🟢"
        elif pct >= 0.35:
            label, icon = "Mixed", "🟡"
        else:
            label, icon = "Weak", "🔴"
        results.append(
            {
                "sector": sector,
                "icon": icon,
                "label": label,
                "bullish": bullish,
                "checked": checked,
                "pct": pct,
                "stocks": len(symbols),
            }
        )
    return sorted(results, key=lambda x: (-x["pct"], x["sector"]))


def format_sector_overview_block() -> str:
    rows = sector_health()
    if not rows:
        return ""
    lines = [
        "<b>📊 All sectors</b> <i>(daily SuperTrend breadth)</i>",
        "",
    ]
    for r in rows:
        lines.append(
            f"{r['icon']} <b>{r['sector']}</b> — {r['label']} "
            f"({r['bullish']}/{r['checked']} bullish, {r['stocks']} stocks)"
        )
    return "\n".join(lines)
