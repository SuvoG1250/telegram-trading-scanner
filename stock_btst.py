"""
Stock BTST (Buy Today Sell Tomorrow) — BUY only, 3:10–3:20 PM IST.

High-signal picks from fundamental quality + stock news + intraday strength.
Sends only confirmed setups with clear overnight gain potential.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import yfinance as yf

from config import (
    STOCK_BTST_ENABLED,
    STOCK_BTST_MAX_ALERTS,
    STOCK_BTST_MIN_CONFIRM_PCT,
    STOCK_BTST_MIN_GAIN_PCT,
)
from data_fetcher import fetch_daily, get_today_session
from indicators import ema
from market_news import build_stock_news_digest
from market_sentiment import assess_market_sentiment
from market_time import is_market_open, is_stock_btst_window, now_ist
from position_lifecycle import equity_position_open, register_equity_open
from risk import levels_playbook
from sector_map import sector_for
from state import load_watchlist, mark_stock_btst_sent, stock_btst_sent
from stocks import to_yfinance_symbol
from telegram_client import Signal, send_signal
from trade_filters import passes_trade_filters
from trade_journal import record_trade

logger = logging.getLogger(__name__)

STRATEGY_NAME = "Stock BTST Overnight"


@dataclass
class StockBtstCandidate:
    symbol: str
    score: float
    confidence_pct: float
    confirmed: bool
    entry: float
    stop: float
    target: float
    gain_pct: float
    checks: list[tuple[str, bool]] = field(default_factory=list)
    news_headlines: list[str] = field(default_factory=list)
    fundamental_note: str = ""


def _fundamental_checks(symbol: str) -> tuple[list[tuple[str, bool]], str, float]:
    """Free fundamental proxy via yfinance info + daily trend."""
    checks: list[tuple[str, bool]] = []
    note_parts: list[str] = []
    score = 0.0

    try:
        info = yf.Ticker(to_yfinance_symbol(symbol)).info or {}
    except Exception:
        info = {}

    pe = info.get("trailingPE") or info.get("forwardPE")
    margin = info.get("profitMargins")
    eg = info.get("earningsGrowth")
    rg = info.get("revenueGrowth")

    if pe and 5 < float(pe) < 60:
        checks.append(("PE in healthy range", True))
        score += 1.0
        note_parts.append(f"PE {float(pe):.1f}")
    else:
        checks.append(("PE in healthy range", False))

    if margin is not None and float(margin) > 0.05:
        checks.append(("Profit margin > 5%", True))
        score += 1.0
        note_parts.append(f"margin {float(margin) * 100:.1f}%")
    else:
        checks.append(("Profit margin > 5%", False))

    if eg is not None and float(eg) > 0:
        checks.append(("Earnings growth positive", True))
        score += 1.2
        note_parts.append(f"earnings +{float(eg) * 100:.0f}%")
    else:
        checks.append(("Earnings growth positive", False))

    if rg is not None and float(rg) > 0:
        checks.append(("Revenue growth positive", True))
        score += 0.8
    else:
        checks.append(("Revenue growth positive", False))

    daily = fetch_daily(symbol, period="6mo")
    if not daily.empty and len(daily) >= 50:
        close = daily["Close"]
        price = float(close.iloc[-1])
        ema20 = float(ema(close, 20).iloc[-1])
        ema50 = float(ema(close, 50).iloc[-1])
        r3m = (price / float(close.iloc[-66]) - 1) * 100 if len(close) >= 66 else 0
        if price > ema20 > ema50:
            checks.append(("Daily trend bullish (EMA20>EMA50)", True))
            score += 1.5
        else:
            checks.append(("Daily trend bullish (EMA20>EMA50)", False))
        if r3m > 5:
            checks.append(("3-month momentum > 5%", True))
            score += 0.5
        else:
            checks.append(("3-month momentum > 5%", False))

    return checks, " · ".join(note_parts) if note_parts else "limited fundamental data", score


def _intraday_strength(symbol: str) -> tuple[list[tuple[str, bool]], float, float, float, float]:
    """Today's session: green day, close near high, volume pick-up."""
    session = get_today_session(symbol, "5m")
    if session.empty or len(session) < 10:
        return [("Intraday data available", False)], 0.0, 0.0, 0.0, 0.0

    day_open = float(session["Open"].iloc[0])
    last = float(session["Close"].iloc[-1])
    day_high = float(session["High"].max())
    day_low = float(session["Low"].min())
    vol = float(session["Volume"].sum())

    day_pct = (last - day_open) / day_open * 100 if day_open > 0 else 0.0
    rng = day_high - day_low
    close_pos = (last - day_low) / rng if rng > 0 else 0.5

    daily = fetch_daily(symbol, period="3mo")
    avg_vol = float(daily["Volume"].tail(20).mean()) if not daily.empty else vol
    vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

    checks = [
        ("Intraday green > 0.5%", day_pct >= 0.5),
        ("Closing in upper 30% of range", close_pos >= 0.70),
        ("Volume above 20-day average", vol_ratio >= 1.1),
    ]
    return checks, last, day_low, day_high, day_pct


def assess_stock_btst(symbol: str) -> StockBtstCandidate | None:
    ok, _ = passes_trade_filters(symbol)
    if not ok:
        return None
    if equity_position_open(symbol):
        return None

    fund_checks, fund_note, fund_score = _fundamental_checks(symbol)
    news = build_stock_news_digest(symbol)
    intraday_checks, entry, day_low, day_high, day_pct = _intraday_strength(symbol)
    if entry <= 0:
        return None

    sentiment = assess_market_sentiment()
    market_ok = sentiment.get("trade_bias") != "bearish"

    levels = levels_playbook(entry, day_low, "BUY")
    if levels is None:
        return None

    gain_pct = levels.target_profit_pct("BUY")
    min_target = entry * (1 + STOCK_BTST_MIN_GAIN_PCT / 100.0)
    if levels.primary_target < min_target:
        from risk import levels_for_long

        risk = max(entry - day_low, entry * 0.004)
        levels = levels_for_long(entry, entry - risk, best_rr=2.0)
        gain_pct = levels.target_profit_pct("BUY")

    checks: list[tuple[str, bool]] = []
    checks.extend(fund_checks)
    checks.append(("Stock news bullish", news.news_bias == "bullish"))
    checks.extend(intraday_checks)
    checks.append(("Market not bearish", market_ok))
    checks.append(
        (
            f"Overnight gain potential ≥ {STOCK_BTST_MIN_GAIN_PCT:.1f}%",
            gain_pct >= STOCK_BTST_MIN_GAIN_PCT,
        )
    )

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    confidence = round(100.0 * passed / total, 0) if total else 0.0
    confirmed = confidence >= STOCK_BTST_MIN_CONFIRM_PCT

    score = fund_score + news.score() * 1.5 + day_pct * 0.3
    if news.news_bias == "bullish":
        score += 1.0

    return StockBtstCandidate(
        symbol=symbol,
        score=round(score, 2),
        confidence_pct=confidence,
        confirmed=confirmed,
        entry=entry,
        stop=levels.stop_loss,
        target=levels.primary_target,
        gain_pct=round(gain_pct, 2),
        checks=checks,
        news_headlines=news.headlines[:4],
        fundamental_note=fund_note,
    )


def _build_signal(candidate: StockBtstCandidate) -> Signal | None:
    levels = levels_playbook(candidate.entry, candidate.stop, "BUY")
    if levels is None:
        return None

    lines = [
        f"<b>STOCK BTST BUY</b> — hold overnight, exit tomorrow morning.",
        f"Confidence: <b>{candidate.confidence_pct:.0f}%</b> · Score {candidate.score:+.2f} · "
        f"Potential <b>+{candidate.gain_pct:.2f}%</b>",
        f"Sector: {sector_for(candidate.symbol)}",
        "",
        "<b>Checks:</b>",
    ]
    for label, ok in candidate.checks:
        mark = "✅" if ok else "❌"
        lines.append(f"{mark} {label}")
    if candidate.fundamental_note:
        lines.extend(["", f"<b>Fundamental:</b> {candidate.fundamental_note}"])
    if candidate.news_headlines:
        lines.extend(["", "<b>News:</b>"])
        for h in candidate.news_headlines[:3]:
            lines.append(f"• {h}")
    lines.append("<i>Overnight gap risk — strict SL. Cash delivery / CNC only.</i>")

    return Signal(
        symbol=candidate.symbol,
        strategy=STRATEGY_NAME,
        side="BUY",
        levels=levels,
        note="\n".join(lines),
        kind="ENTRY",
        timeframe="BTST",
        timestamp=now_ist().strftime("%d %b %Y, %H:%M IST"),
    )


def _candidate_watchlist() -> list[str]:
    wl = load_watchlist()
    if wl:
        return wl[:60]
    from playbook_selection import build_playbook_watchlist

    selected, _ = build_playbook_watchlist()
    return selected


def run_stock_btst_alerts() -> int:
    """Scan watchlist; send up to STOCK_BTST_MAX_ALERTS confirmed BUY BTST signals."""
    if not STOCK_BTST_ENABLED or not is_market_open() or not is_stock_btst_window():
        return 0
    if stock_btst_sent():
        logger.info("Stock BTST already sent today.")
        return 0

    candidates: list[StockBtstCandidate] = []
    for symbol in _candidate_watchlist():
        try:
            c = assess_stock_btst(symbol)
        except Exception:
            logger.debug("Stock BTST assess failed for %s", symbol, exc_info=True)
            continue
        if c and c.confirmed:
            candidates.append(c)

    if not candidates:
        logger.info("Stock BTST: no confirmed candidates today.")
        mark_stock_btst_sent()
        return 0

    candidates.sort(key=lambda x: (-x.confidence_pct, -x.score))
    sent = 0
    sent_syms: list[str] = []

    for c in candidates[:STOCK_BTST_MAX_ALERTS]:
        sig = _build_signal(c)
        if sig is None:
            continue
        if send_signal(sig):
            register_equity_open(sig, STRATEGY_NAME)
            record_trade(sig)
            sent += 1
            sent_syms.append(c.symbol)
            logger.info(
                "Stock BTST sent: %s +%.2f%% potential (%.0f%% confirm)",
                c.symbol,
                c.gain_pct,
                c.confidence_pct,
            )

    mark_stock_btst_sent(sent_syms)
    return sent
