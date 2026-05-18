"""Long-term investment suggestions with suggested holding duration."""

from __future__ import annotations

import logging

import pandas as pd

from config import SUPERTREND_LENGTH, SUPERTREND_MULTIPLIER
from data_fetcher import fetch_daily
from indicators import ema, supertrend_direction
from sector_map import sector_for
from stocks import get_scan_universe

logger = logging.getLogger(__name__)

HORIZONS = [
    ("6-12 months", "short", 126, 252),
    ("1-3 years", "medium", 252, 504),
    ("3-5+ years", "long", 504, 1260),
]


def _returns(close: pd.Series, days: int) -> float | None:
    if len(close) < days + 5:
        return None
    old = float(close.iloc[-days])
    if old <= 0:
        return None
    return (float(close.iloc[-1]) / old) - 1.0


def score_long_term(symbol: str) -> dict | None:
    daily = fetch_daily(symbol, period="5y")
    if daily.empty or len(daily) < 260:
        return None

    close = daily["Close"]
    price = float(close.iloc[-1])
    if price < 50:
        return None

    st_dir = supertrend_direction(
        daily, length=SUPERTREND_LENGTH, multiplier=SUPERTREND_MULTIPLIER
    )
    st_bull = float(st_dir.iloc[-1]) > 0
    ema200 = float(ema(close, 200).iloc[-1]) if len(close) >= 200 else price
    above_200 = price > ema200

    r6m = _returns(close, 126)
    r1y = _returns(close, 252)
    r3y = _returns(close, min(756, len(close) - 5))

    if r1y is None:
        return None

    high_52w = float(daily["High"].tail(252).max())
    dist_high = ((high_52w - price) / high_52w * 100) if high_52w > 0 else 0

    score = 0.0
    if st_bull:
        score += 2
    if above_200:
        score += 2
    if r1y and r1y > 0.10:
        score += 2
    if r6m and r6m > 0.05:
        score += 1
    if r3y and r3y > 0.30:
        score += 2
    if dist_high < 8:
        score += 1

    # Assign primary horizon
    if r6m and r6m > 0.15 and st_bull and dist_high < 12:
        horizon = "6-12 months"
        reason = "Strong momentum + trend; suited for swing-to-medium hold."
    elif r3y and r3y > 0.40 and above_200 and st_bull:
        horizon = "3-5+ years"
        reason = "Multi-year uptrend, above 200 DMA — compounder-style hold."
    elif r1y and r1y > 0.08 and above_200:
        horizon = "1-3 years"
        reason = "Steady annual trend with structural support."
    else:
        horizon = "1-3 years"
        reason = "Quality trend; review quarterly."

    return {
        "symbol": symbol,
        "sector": sector_for(symbol),
        "price": round(price, 2),
        "horizon": horizon,
        "reason": reason,
        "score": score,
        "r1y_pct": round((r1y or 0) * 100, 1),
        "st_bull": st_bull,
        "above_200": above_200,
    }


def build_long_term_picks(
    universe: list[str] | None = None,
    per_horizon: int = 3,
) -> dict[str, list[dict]]:
    universe = universe or get_scan_universe()
    scored: list[dict] = []
    for sym in universe:
        try:
            row = score_long_term(sym)
            if row and row["score"] >= 4:
                scored.append(row)
        except Exception:
            logger.exception("LT score failed for %s", sym)

    scored.sort(key=lambda x: x["score"], reverse=True)
    buckets: dict[str, list[dict]] = {h[0]: [] for h in HORIZONS}
    for row in scored:
        h = row["horizon"]
        if len(buckets.get(h, [])) < per_horizon:
            buckets.setdefault(h, []).append(row)

    # Fill empty horizons from next best
    for row in scored:
        for h in buckets:
            if len(buckets[h]) < per_horizon and row not in buckets[h]:
                if all(row["symbol"] != x["symbol"] for vals in buckets.values() for x in vals):
                    row = {**row, "horizon": h}
                    buckets[h].append(row)
        if all(len(buckets[h]) >= per_horizon for h in buckets):
            break

    return buckets


def format_long_term_message(picks: dict[str, list[dict]]) -> str:
    lines = [
        "<b>📈 Long-term investment ideas</b>",
        "<i>Not intraday — suggested holding duration (research + risk management required).</i>",
        "",
    ]
    for title, _, _, _ in HORIZONS:
        rows = picks.get(title, [])
        lines.append(f"<b>⏳ {title}</b>")
        if not rows:
            lines.append("• No pick met filters today.")
        else:
            for r in rows:
                trend = "🟢 ST up" if r["st_bull"] else "🔴 ST down"
                dma = "above 200 DMA" if r["above_200"] else "below 200 DMA"
                lines.append(
                    f"• <b>{r['symbol']}</b> ({r['sector']}) @ ₹{r['price']:,.2f}\n"
                    f"  Hold: <b>{r['horizon']}</b> | 1Y: {r['r1y_pct']:+.1f}% | {trend}, {dma}\n"
                    f"  <i>{r['reason']}</i>"
                )
        lines.append("")
    lines.append(
        "<i>Disclaimer: Educational scan only — not SEBI-registered advice. "
        "Verify fundamentals before investing.</i>"
    )
    return "\n".join(lines).strip()
