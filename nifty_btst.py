"""
Nifty BTST (Buy Today Sell Tomorrow) — one alert between 3:20–3:30 PM IST.

Combines: market sentiment, headline scan (yfinance + Google News RSS),
intraday Nifty trend, Supertrend direction. Optional Gemini summary if API key set.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import pandas as pd
import yfinance as yf

from config import (
    GEMINI_API_KEY,
    NIFTY_BTST_ENABLED,
    NIFTY_EXIT490_ATR_BARS,
    NIFTY_EXIT490_ATR_MULT,
    NIFTY_OPTION_PREMIUM_SL_POINTS,
    NIFTY_OPTION_PREMIUM_TARGET_POINTS,
    NIFTY_OPTION_PREMIUM_TRAIL_MAX_POINTS,
    NIFTY_ST_ATR_LENGTH,
    NIFTY_ST_ENGINE,
    NIFTY_ST_FACTOR,
    NIFTY_ST_INTERVAL,
    NIFTY_STRIKE_STEP,
)
from indicators import atr, compute_supertrend, compute_supertrend_exit490
from market_news import build_market_news_digest, format_headlines_for_telegram
from market_sentiment import NIFTY_TICKER, assess_market_sentiment
from market_time import is_market_open, is_nifty_btst_window, now_ist
from nifty_options import (
    _estimate_atm_premium,
    _fetch_nifty_session,
    _option_levels_points,
    _round_strike,
    _underlying_targets,
    _weekly_expiry_label,
)
from option_quotes import fetch_nifty_option_quote as get_option_quote
from state import mark_nifty_btst_sent, nifty_btst_sent
from telegram_client import Signal

logger = logging.getLogger(__name__)

STRATEGY_NAME = "Nifty BTST Overnight"


def _nifty_intraday_bias() -> tuple[float, str]:
    """Day change % and label at scan time."""
    try:
        df = yf.Ticker(NIFTY_TICKER).history(period="1d", interval="5m", auto_adjust=True)
    except Exception:
        return 0.0, "unknown"
    if df.empty:
        return 0.0, "unknown"
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.capitalize)
    day_open = float(df["Open"].iloc[0])
    last = float(df["Close"].iloc[-1])
    if day_open <= 0:
        return 0.0, "flat"
    pct = (last - day_open) / day_open * 100
    if pct > 0.25:
        return round(pct, 2), "up"
    if pct < -0.25:
        return round(pct, 2), "down"
    return round(pct, 2), "flat"


def _supertrend_bias(session: pd.DataFrame) -> str:
    if len(session) < 10:
        return "neutral"
    if NIFTY_ST_ENGINE == "exit490":
        st = compute_supertrend_exit490(
            session,
            bars_back=NIFTY_EXIT490_ATR_BARS,
            mult=NIFTY_EXIT490_ATR_MULT,
        )
        direction = -int(st["direction"].iloc[-1])
    else:
        st = compute_supertrend(session, length=NIFTY_ST_ATR_LENGTH, multiplier=NIFTY_ST_FACTOR)
        direction = int(st["direction"].iloc[-1])
    return "bullish" if direction > 0 else "bearish"


def _optional_gemini_summary(headlines: list[str], sentiment: dict, bias: str) -> str:
    if not GEMINI_API_KEY or not headlines:
        return ""
    try:
        import urllib.request

        prompt = (
            "You are an Indian NSE Nifty options analyst. In 3 short bullet points, "
            f"summarize market mood for a BTST (overnight) {bias} trade. "
            f"Sentiment: {sentiment.get('summary', '')}. "
            f"Headlines: {' | '.join(headlines[:8])}"
        )
        body = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 220, "temperature": 0.3},
            }
        ).encode("utf-8")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        )
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read())
        parts = data["candidates"][0]["content"]["parts"]
        text = parts[0].get("text", "").strip()
        return text[:500] if text else ""
    except Exception:
        logger.warning("Gemini BTST summary skipped (API error or no key).")
        return ""


def research_btst_market() -> dict:
    """Full sentiment + news + technical stack for 3:20 PM decision."""
    sentiment = assess_market_sentiment()
    news = build_market_news_digest()
    day_pct, day_dir = _nifty_intraday_bias()

    interval = NIFTY_ST_INTERVAL if NIFTY_ST_INTERVAL in ("5m", "15m") else "5m"
    session = _fetch_nifty_session(interval)
    st_bias = _supertrend_bias(session)

    score = 0.0
    bias_map = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "positive": 0.6, "negative": -0.6}
    score += bias_map.get(sentiment.get("trade_bias", "neutral"), 0) * 1.5
    score += bias_map.get(sentiment.get("global", "mixed"), 0) * 0.8
    score += news.score() * 1.2
    if day_dir == "up":
        score += 1.0
    elif day_dir == "down":
        score -= 1.0
    if st_bias == "bullish":
        score += 1.2
    elif st_bias == "bearish":
        score -= 1.2

    if score >= 1.0:
        decision = "CALL"
    elif score <= -1.0:
        decision = "PUT"
    else:
        # Tie-break: intraday + supertrend
        if day_pct >= 0 and st_bias == "bullish":
            decision = "CALL"
        elif day_pct <= 0 and st_bias == "bearish":
            decision = "PUT"
        else:
            decision = "CALL" if day_pct >= 0 else "PUT"

    gemini = _optional_gemini_summary(news.headlines, sentiment, decision)

    return {
        "decision": decision,
        "score": round(score, 2),
        "sentiment": sentiment,
        "news": news,
        "day_pct": day_pct,
        "day_dir": day_dir,
        "st_bias": st_bias,
        "gemini_note": gemini,
        "session": session,
    }


def _build_btst_note(research: dict) -> str:
    s = research["sentiment"]
    n = research["news"]
    lines = [
        "<b>BTST</b> — buy today, exit tomorrow morning (overnight risk).",
        f"Research score: {research['score']:+.2f} → <b>{research['decision']}</b>",
        "",
        f"<b>Sentiment:</b> {s.get('trade_bias', 'neutral')} | "
        f"Nifty gap {s.get('nifty_gap_pct', 0):+.2f}% | Global {s.get('global', 'mixed')}",
        f"<b>Intraday Nifty:</b> {research['day_pct']:+.2f}% ({research['day_dir']}) | "
        f"ST: {research['st_bias']}",
        f"<b>News scan:</b> {n.news_bias} "
        f"(+{n.bullish_hits} bull / +{n.bearish_hits} bear keywords)",
        "",
        "<b>Headlines:</b>",
        format_headlines_for_telegram(n, max_lines=4),
    ]
    if research.get("gemini_note"):
        lines.extend(["", "<b>AI summary:</b>", research["gemini_note"][:400]])
    lines.append(
        "<i>Not financial advice. Verify FII/DII on NSE. Gap risk overnight.</i>"
    )
    return "\n".join(lines)


def scan_nifty_btst() -> Signal | None:
    if not NIFTY_BTST_ENABLED:
        return None
    if not is_market_open() or not is_nifty_btst_window():
        return None
    if nifty_btst_sent():
        logger.info("Nifty BTST already sent today.")
        return None

    research = research_btst_market()
    flip = research["decision"]
    session = research["session"]
    if session.empty or len(session) < 5:
        logger.warning("BTST skip — no Nifty session bars.")
        return None

    spot = float(session["Close"].iloc[-1])
    if NIFTY_ST_ENGINE == "exit490":
        st_raw = compute_supertrend_exit490(
            session,
            bars_back=NIFTY_EXIT490_ATR_BARS,
            mult=NIFTY_EXIT490_ATR_MULT,
        )
    else:
        st_raw = compute_supertrend(session, length=NIFTY_ST_ATR_LENGTH, multiplier=NIFTY_ST_FACTOR)
    st_line = float(st_raw["st_line"].iloc[-1])

    strike = _round_strike(spot)
    opt_type = "CE" if flip == "CALL" else "PE"
    premium_source = "estimate"
    quote, src = get_option_quote(strike, opt_type)
    if quote:
        premium = quote.last_price
        premium_source = src
        if quote.spot:
            spot = quote.spot
        exp_raw = (quote.expiry or "").strip()
        if exp_raw:
            try:
                expiry = datetime.strptime(exp_raw, "%Y-%m-%d").strftime("%d %b %Y")
            except ValueError:
                expiry = _weekly_expiry_label()
        else:
            expiry = _weekly_expiry_label()
    else:
        expiry = _weekly_expiry_label()
        atr_len = NIFTY_ST_ATR_LENGTH if NIFTY_ST_ENGINE != "exit490" else max(NIFTY_EXIT490_ATR_BARS, 1)
        atr_val = float(atr(session["High"], session["Low"], session["Close"], atr_len).iloc[-1])
        premium = _estimate_atm_premium(spot, atr_val)

    levels = _option_levels_points(premium)
    idx_sl, idx_target = _underlying_targets(spot, st_line, flip)
    action = f"BUY {'CALL' if flip == 'CALL' else 'PUT'} BTST"

    return Signal(
        symbol=f"NIFTY {strike} {opt_type}",
        strategy=STRATEGY_NAME,
        side=action,
        levels=levels,
        note=_build_btst_note(research),
        kind="ENTRY",
        timeframe="BTST",
        timestamp=now_ist().strftime("%d %b %Y, %H:%M IST"),
        instrument="NIFTY_OPTION",
        strike=float(strike),
        option_type=opt_type,
        expiry_label=expiry,
        underlying=spot,
        underlying_sl=idx_sl,
        underlying_target=idx_target,
        premium_source=premium_source,
        option_points_mode=True,
    )


def run_nifty_btst_alert() -> Signal | None:
    """Send BTST Telegram once; mark session state."""
    sig = scan_nifty_btst()
    if sig is None:
        return None
    from telegram_client import send_signal
    from trade_journal import record_trade

    if send_signal(sig):
        mark_nifty_btst_sent()
        record_trade(sig)
        logger.info("Nifty BTST alert sent: %s", sig.side)
        return sig
    logger.error("Nifty BTST Telegram send failed.")
    return None
