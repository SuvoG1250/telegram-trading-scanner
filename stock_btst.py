"""
Stock Gap-Up BTST (Buy Today Sell Tomorrow) — BUY only, 3:10–3:20 PM IST.

Scans all NSE stocks under Rs 1000 for high fundamental + news + gap-up potential.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

from config import (
    STOCK_BTST_ENABLED,
    STOCK_BTST_MAX_ALERTS,
    STOCK_BTST_MAX_PRICE,
    STOCK_BTST_MIN_CONFIRM_PCT,
    STOCK_BTST_MIN_GAIN_PCT,
    STOCK_BTST_MIN_GAPUP_SCORE,
    STOCK_BTST_MIN_PRICE,
    STOCK_BTST_SCREEN_TOP,
    YFINANCE_SUFFIX,
)
from data_fetcher import fetch_daily, get_today_session
from indicators import ema
from market_news import build_stock_news_digest
from market_sentiment import assess_market_sentiment
from market_time import is_market_open, is_stock_btst_window, now_ist
from position_lifecycle import equity_position_open, register_equity_open
from risk import levels_for_long, levels_playbook
from sector_map import sector_for
from state import mark_stock_btst_sent, stock_btst_sent
from stocks import load_nse_equity_symbols, to_yfinance_symbol
from telegram_client import Signal, send_signal
from trade_journal import record_trade

logger = logging.getLogger(__name__)

STRATEGY_NAME = "Stock Gap-Up BTST"
_BATCH_CHUNK = 25


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
    gapup_score: float = 0.0
    checks: list[tuple[str, bool]] = field(default_factory=list)
    news_headlines: list[str] = field(default_factory=list)
    fundamental_note: str = ""


def _extract_last_close(data, symbol: str) -> float | None:
    yf_sym = f"{symbol}{YFINANCE_SUFFIX}"
    if data is None or (hasattr(data, "empty") and data.empty):
        return None
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if yf_sym in data.columns.get_level_values(0):
                close = data[(yf_sym, "Close")].dropna()
            else:
                return None
        else:
            close = data["Close"].dropna()
        if len(close):
            return float(close.iloc[-1])
    except Exception:
        return None
    return None


def _filter_nse_under_max_price(symbols: list[str]) -> list[str]:
    """Keep NSE EQ symbols with last close in STOCK_BTST_MIN/MAX_PRICE (batch yfinance)."""
    matched: list[str] = []
    for i in range(0, len(symbols), _BATCH_CHUNK):
        chunk = symbols[i : i + _BATCH_CHUNK]
        tickers = [f"{s}{YFINANCE_SUFFIX}" for s in chunk]
        try:
            data = yf.download(
                tickers,
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=False,
                progress=False,
            )
        except Exception:
            logger.warning("BTST price batch failed at %s", chunk[0] if chunk else "?")
            time.sleep(1.0)
            continue
        for sym in chunk:
            px = _extract_last_close(data, sym)
            if px is not None and STOCK_BTST_MIN_PRICE <= px <= STOCK_BTST_MAX_PRICE:
                matched.append(sym)
        if i + _BATCH_CHUNK < len(symbols):
            time.sleep(0.8)
        if i and i % 200 == 0:
            logger.info("BTST price filter: %s / %s (matched %s)", i, len(symbols), len(matched))
    return sorted(set(matched))


def _gapup_screen_score(session: pd.DataFrame) -> tuple[float, float, float, float, float]:
    """Score gap-up potential from today's 5m session."""
    if session.empty or len(session) < 8:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    day_open = float(session["Open"].iloc[0])
    last = float(session["Close"].iloc[-1])
    day_high = float(session["High"].max())
    day_low = float(session["Low"].min())
    vol = float(session["Volume"].sum())

    day_pct = (last - day_open) / day_open * 100 if day_open > 0 else 0.0
    rng = day_high - day_low
    close_pos = (last - day_low) / rng if rng > 0 else 0.5
    dist_high_pct = (day_high - last) / day_high * 100 if day_high > 0 else 100.0

    score = 0.0
    if day_pct >= 1.0:
        score += 2.0
    elif day_pct >= 0.5:
        score += 1.0
    if close_pos >= 0.85:
        score += 2.0
    elif close_pos >= 0.70:
        score += 1.0
    if dist_high_pct <= 0.5:
        score += 1.5
    elif dist_high_pct <= 1.5:
        score += 0.8

    if len(session) >= 12:
        last_hour = session.tail(12)
        lh_open = float(last_hour["Open"].iloc[0])
        lh_close = float(last_hour["Close"].iloc[-1])
        if lh_open > 0 and lh_close > lh_open:
            score += 1.0

    return score, last, day_low, day_high, day_pct


def _screen_gapup_candidates(symbols: list[str]) -> list[tuple[str, float]]:
    """Fast batch screen — return top symbols by gap-up score."""
    ranked: list[tuple[str, float]] = []
    for i in range(0, len(symbols), _BATCH_CHUNK):
        chunk = symbols[i : i + _BATCH_CHUNK]
        tickers = [f"{s}{YFINANCE_SUFFIX}" for s in chunk]
        try:
            data = yf.download(
                tickers,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=True,
                threads=False,
                progress=False,
            )
        except Exception:
            time.sleep(1.0)
            continue

        for sym in chunk:
            yf_sym = f"{sym}{YFINANCE_SUFFIX}"
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if yf_sym not in data.columns.get_level_values(0):
                        continue
                    sess = data[yf_sym].dropna()
                else:
                    sess = data.dropna()
                if sess.empty:
                    continue
                gscore, *_ = _gapup_screen_score(sess)
                if gscore >= STOCK_BTST_MIN_GAPUP_SCORE:
                    ranked.append((sym, gscore))
            except Exception:
                continue
        if i + _BATCH_CHUNK < len(symbols):
            time.sleep(0.6)

    ranked.sort(key=lambda x: -x[1])
    return ranked[:STOCK_BTST_SCREEN_TOP]


def _fundamental_checks(symbol: str) -> tuple[list[tuple[str, bool]], str, float]:
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
        if price > STOCK_BTST_MAX_PRICE:
            return checks, "above Rs 1000", -99.0
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


def _gapup_checks(
    session: pd.DataFrame,
    day_pct: float,
    day_high: float,
    last: float,
    sentiment: dict,
) -> tuple[list[tuple[str, bool]], float]:
    checks: list[tuple[str, bool]] = []
    gscore, _, day_low, _, _ = _gapup_screen_score(session)

    dist_high = (day_high - last) / day_high * 100 if day_high > 0 else 100.0
    rng = day_high - day_low if day_high > day_low else 1.0
    close_pos = (last - day_low) / rng

    checks.append(("Strong green day (≥0.5%)", day_pct >= 0.5))
    checks.append(("Close near day high (≤1.5% below)", dist_high <= 1.5))
    checks.append(("Close in upper 30% of range", close_pos >= 0.70))
    checks.append(("Gap-up momentum score strong", gscore >= STOCK_BTST_MIN_GAPUP_SCORE))
    checks.append(("Nifty/market not bearish", sentiment.get("trade_bias") != "bearish"))
    checks.append(
        (
            "Global sentiment supportive",
            sentiment.get("global") in ("positive", "mixed"),
        )
    )

    return checks, gscore


def _intraday_session(symbol: str) -> tuple[pd.DataFrame, float, float, float, float]:
    session = get_today_session(symbol, "5m")
    if session.empty:
        return session, 0.0, 0.0, 0.0, 0.0
    _, last, day_low, day_high, day_pct = _gapup_screen_score(session)
    return session, last, day_low, day_high, day_pct


def assess_stock_btst(symbol: str, *, prescreen_gapup: float = 0.0) -> StockBtstCandidate | None:
    if equity_position_open(symbol):
        return None

    session, entry, day_low, day_high, day_pct = _intraday_session(symbol)
    if entry <= 0 or entry > STOCK_BTST_MAX_PRICE or entry < STOCK_BTST_MIN_PRICE:
        return None

    daily = fetch_daily(symbol, period="3mo")
    if not daily.empty:
        avg_vol = float(daily["Volume"].tail(22).mean())
        if avg_vol < 200_000:
            return None

    fund_checks, fund_note, fund_score = _fundamental_checks(symbol)
    if fund_score <= -50:
        return None

    news = build_stock_news_digest(symbol)
    sentiment = assess_market_sentiment()
    if session.empty:
        return None
    gap_checks, gapup_score = _gapup_checks(session, day_pct, day_high, entry, sentiment)

    levels = levels_playbook(entry, day_low, "BUY")
    if levels is None:
        return None

    gain_pct = levels.target_profit_pct("BUY")
    if gain_pct < STOCK_BTST_MIN_GAIN_PCT:
        risk = max(entry - day_low, entry * 0.004)
        levels = levels_for_long(entry, entry - risk, best_rr=2.0)
        gain_pct = levels.target_profit_pct("BUY")

    checks: list[tuple[str, bool]] = []
    checks.extend(gap_checks)
    checks.extend(fund_checks)
    checks.append(("Stock news bullish / catalyst", news.news_bias == "bullish"))
    checks.append(
        (
            f"Overnight gap-up potential ≥ {STOCK_BTST_MIN_GAIN_PCT:.1f}%",
            gain_pct >= STOCK_BTST_MIN_GAIN_PCT,
        )
    )

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    confidence = round(100.0 * passed / total, 0) if total else 0.0
    confirmed = confidence >= STOCK_BTST_MIN_CONFIRM_PCT

    score = (
        gapup_score * 1.2
        + fund_score
        + news.score() * 1.5
        + day_pct * 0.4
        + prescreen_gapup * 0.3
    )
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
        gapup_score=round(gapup_score, 2),
        checks=checks,
        news_headlines=news.headlines[:4],
        fundamental_note=fund_note,
    )


def _build_signal(candidate: StockBtstCandidate) -> Signal | None:
    levels = levels_playbook(candidate.entry, candidate.stop, "BUY")
    if levels is None:
        return None

    lines = [
        f"<b>STOCK GAP-UP BTST BUY</b> — potential gap-up tomorrow morning.",
        f"Price: <b>Rs {candidate.entry:,.2f}</b> (under Rs {STOCK_BTST_MAX_PRICE:.0f})",
        f"Gap-up score: <b>{candidate.gapup_score:.1f}</b> · Confidence: "
        f"<b>{candidate.confidence_pct:.0f}%</b> · Potential <b>+{candidate.gain_pct:.2f}%</b>",
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
    lines.append("<i>Exit tomorrow open if gap-up. CNC/delivery only · strict SL.</i>")

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


def _btst_universe() -> list[str]:
    """All NSE EQ with last close Rs 50–1000."""
    all_eq = load_nse_equity_symbols()
    logger.info("Stock BTST: filtering %s NSE symbols under Rs %.0f…", len(all_eq), STOCK_BTST_MAX_PRICE)
    under = _filter_nse_under_max_price(all_eq)
    logger.info("Stock BTST universe: %s symbols under Rs %.0f", len(under), STOCK_BTST_MAX_PRICE)
    return under


def run_stock_btst_alerts() -> int:
    """Scan all NSE under Rs 1000 for gap-up BTST; send top confirmed BUY picks."""
    if not STOCK_BTST_ENABLED or not is_market_open() or not is_stock_btst_window():
        return 0
    if stock_btst_sent():
        logger.info("Stock BTST already sent today.")
        return 0

    universe = _btst_universe()
    if not universe:
        logger.warning("Stock BTST: empty universe.")
        mark_stock_btst_sent()
        return 0

    screened = _screen_gapup_candidates(universe)
    logger.info(
        "Stock BTST gap-up screen: %s candidates from %s symbols",
        len(screened),
        len(universe),
    )

    if not screened:
        logger.info("Stock BTST: no gap-up candidates today.")
        mark_stock_btst_sent()
        return 0

    candidates: list[StockBtstCandidate] = []
    for symbol, gprescore in screened:
        try:
            c = assess_stock_btst(symbol, prescreen_gapup=gprescore)
        except Exception:
            logger.debug("Stock BTST assess failed for %s", symbol, exc_info=True)
            continue
        if c and c.confirmed:
            candidates.append(c)

    if not candidates:
        logger.info("Stock BTST: no confirmed gap-up picks after full research.")
        mark_stock_btst_sent()
        return 0

    candidates.sort(key=lambda x: (-x.gapup_score, -x.confidence_pct, -x.score))
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
                "Stock Gap-Up BTST sent: %s gap=%.1f +%.2f%% (%.0f%% confirm)",
                c.symbol,
                c.gapup_score,
                c.gain_pct,
                c.confidence_pct,
            )

    mark_stock_btst_sent(sent_syms)
    return sent
