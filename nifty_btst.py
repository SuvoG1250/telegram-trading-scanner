"""
Nifty BTST (Buy Today Sell Tomorrow) — one alert between 3:20–3:30 PM IST.

100% confirmed → BUY CALL or PUT with full research.
Not confirmed → "BTST is risky today — do not take BTST".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import yfinance as yf

from config import (
    GEMINI_API_KEY,
    NIFTY_BTST_ENABLED,
    NIFTY_BTST_MIN_SCORE,
    NIFTY_EXIT490_ATR_BARS,
    NIFTY_EXIT490_ATR_MULT,
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
from telegram_client import Signal, send_plain

logger = logging.getLogger(__name__)

STRATEGY_NAME = "Nifty BTST Overnight"


@dataclass
class BtstConfirmation:
    confirmed: bool
    decision: str
    score: float
    confidence_pct: float
    checks: list[tuple[str, bool]] = field(default_factory=list)
    sentiment: dict = field(default_factory=dict)
    news: object = None
    day_pct: float = 0.0
    day_dir: str = "flat"
    st_bias: str = "neutral"
    gemini_note: str = ""
    session: pd.DataFrame = field(default_factory=pd.DataFrame)


def _nifty_intraday_bias() -> tuple[float, str]:
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
        logger.warning("Gemini BTST summary skipped.")
        return ""


def _preliminary_decision(score: float, day_pct: float, st_bias: str) -> str:
    if score >= 1.0:
        return "CALL"
    if score <= -1.0:
        return "PUT"
    if day_pct >= 0 and st_bias == "bullish":
        return "CALL"
    if day_pct <= 0 and st_bias == "bearish":
        return "PUT"
    return "CALL" if day_pct >= 0 else "PUT"


def assess_btst_confirmation() -> BtstConfirmation:
    """Score market + require ALL checks to pass for 100% BTST confirm."""
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

    score = round(score, 2)
    decision = _preliminary_decision(score, day_pct, st_bias)
    s = sentiment

    if decision == "CALL":
        checks: list[tuple[str, bool]] = [
            ("Nifty sentiment bullish", s.get("trade_bias") == "bullish"),
            ("Global markets positive", s.get("global") == "positive"),
            ("News scan bullish", news.news_bias == "bullish"),
            ("Intraday Nifty trending up", day_dir == "up"),
            ("Supertrend bullish (green)", st_bias == "bullish"),
            ("Research score strong", score >= NIFTY_BTST_MIN_SCORE),
        ]
    else:
        checks = [
            ("Nifty sentiment bearish", s.get("trade_bias") == "bearish"),
            ("Global markets negative", s.get("global") == "negative"),
            ("News scan bearish", news.news_bias == "bearish"),
            ("Intraday Nifty trending down", day_dir == "down"),
            ("Supertrend bearish (red)", st_bias == "bearish"),
            ("Research score strong", score <= -NIFTY_BTST_MIN_SCORE),
        ]

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    confidence = round(100.0 * passed / total, 0) if total else 0.0
    confirmed = passed == total

    gemini = _optional_gemini_summary(news.headlines, sentiment, decision) if confirmed else ""

    return BtstConfirmation(
        confirmed=confirmed,
        decision=decision,
        score=score,
        confidence_pct=confidence,
        checks=checks,
        sentiment=sentiment,
        news=news,
        day_pct=day_pct,
        day_dir=day_dir,
        st_bias=st_bias,
        gemini_note=gemini,
        session=session,
    )


def _build_btst_note(assessment: BtstConfirmation) -> str:
    s = assessment.sentiment
    n = assessment.news
    lines = [
        "<b>BTST CONFIRMED</b> — buy today, exit tomorrow morning.",
        f"Confidence: <b>{assessment.confidence_pct:.0f}%</b> · Score {assessment.score:+.2f} → <b>{assessment.decision}</b>",
        "",
        "<b>All checks passed:</b>",
    ]
    for label, ok in assessment.checks:
        mark = "✅" if ok else "❌"
        lines.append(f"{mark} {label}")
    lines.extend(
        [
            "",
            f"<b>Sentiment:</b> {s.get('trade_bias')} | Gap {s.get('nifty_gap_pct', 0):+.2f}% | Global {s.get('global')}",
            f"<b>Intraday:</b> {assessment.day_pct:+.2f}% ({assessment.day_dir}) | ST: {assessment.st_bias}",
            "",
            "<b>Headlines:</b>",
            format_headlines_for_telegram(n, max_lines=3),
        ]
    )
    if assessment.gemini_note:
        lines.extend(["", "<b>AI summary:</b>", assessment.gemini_note[:400]])
    lines.append("<i>Overnight gap risk — use strict SL on premium.</i>")
    return "\n".join(lines)


def format_btst_risky_message(assessment: BtstConfirmation) -> str:
    ts = now_ist().strftime("%d %b %Y, %H:%M IST")
    lines = [
        f"⚠️ <b>BTST RISKY TODAY — DO NOT TAKE BTST</b>",
        f"<i>{ts}</i>",
        "",
        f"Confirmation: <b>{assessment.confidence_pct:.0f}%</b> (need 100% for CALL/PUT alert)",
        f"Preliminary bias was <b>{assessment.decision}</b> but setup is <b>not fully confirmed</b>.",
        "",
        "<b>Checklist:</b>",
    ]
    for label, ok in assessment.checks:
        mark = "✅" if ok else "❌"
        lines.append(f"{mark} {label}")
    lines.extend(
        [
            "",
            f"Score: {assessment.score:+.2f} · Intraday Nifty {assessment.day_pct:+.2f}% ({assessment.day_dir})",
            "<b>Action:</b> Skip overnight BTST today. Wait for next session.",
            "<i>Intraday stock/options signals are unchanged.</i>",
        ]
    )
    return "\n".join(lines)


def _build_confirmed_signal(assessment: BtstConfirmation) -> Signal | None:
    session = assessment.session
    if session.empty or len(session) < 5:
        logger.warning("BTST confirmed but no Nifty bars.")
        return None

    flip = assessment.decision
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
        note=_build_btst_note(assessment),
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
    """One BTST message per day: confirmed trade OR risky warning."""
    if not NIFTY_BTST_ENABLED:
        return None
    if not is_market_open() or not is_nifty_btst_window():
        return None
    if nifty_btst_sent():
        logger.info("Nifty BTST already sent today.")
        return None

    assessment = assess_btst_confirmation()

    from telegram_client import send_signal
    from trade_journal import record_trade

    if assessment.confirmed:
        sig = _build_confirmed_signal(assessment)
        if sig is None:
            return None
        if send_signal(sig):
            mark_nifty_btst_sent()
            record_trade(sig)
            logger.info("BTST CONFIRMED alert sent: %s", sig.side)
            return sig
        logger.error("BTST confirmed Telegram send failed.")
        return None

    risky_text = format_btst_risky_message(assessment)
    if send_plain(risky_text, html_mode=True):
        mark_nifty_btst_sent()
        logger.info("BTST RISKY warning sent (%.0f%% confirm).", assessment.confidence_pct)
        return None
    logger.error("BTST risky warning Telegram send failed.")
    return None
