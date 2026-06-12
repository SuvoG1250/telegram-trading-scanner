"""
Index BTST / STBT — probability gap model for Nifty 50 & Sensex.

Window: 3:15–3:25 PM IST.
Analyzes: GIFT premium, US futures, 15m/daily structure, option PCR/OI, event risk.
Output: Gap Up/Down/Flat probabilities + HOLD CE / HOLD PE / NO TRADE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import pandas as pd
import yfinance as yf

from config import (
    INDEX_BTST_LOT_GUIDANCE_PCT,
    INDEX_BTST_MIN_GAP_PROB,
    UPSTOX_NIFTY_INSTRUMENT_KEY,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
    upstox_nifty_lot_size,
    upstox_sensex_lot_size,
)
from data_fetcher import fetch_index_history
from indicators import atr, ema
from index_options import (
    _estimate_atm_premium,
    _option_levels_points,
    _round_strike,
    _underlying_targets,
    _weekly_expiry_label,
)
from market_news import build_market_news_digest, detect_high_impact_events
from market_sentiment import GLOBAL_TICKERS
from market_time import is_index_btst_window, is_market_open, now_ist
from option_quotes import fetch_nifty_option_quote, fetch_sensex_option_quote
from telegram_client import Signal, send_plain
from upstox_api import fetch_expiries, fetch_option_chain, nearest_expiry, upstox_configured

logger = logging.getLogger(__name__)

QuoteFn = Callable[[int, str], tuple]


@dataclass(frozen=True)
class IndexBtstSpec:
    key: str
    label: str
    yf_ticker: str
    gift_tickers: tuple[str, ...]
    instrument: str
    upstox_key: str
    strike_step: int
    expiry_weekday: int
    lot_size: int
    fetch_quote: QuoteFn
    sent_check: Callable[[], bool]
    mark_sent: Callable[[], None]
    strategy_name: str = "Index Gap Probability BTST"


@dataclass
class GapAssessment:
    gap_up_pct: float
    gap_down_pct: float
    flat_pct: float
    action: str
    spot: float
    gift: float | None
    gift_premium_pts: float | None
    dow_pct: float
    nasdaq_pct: float
    structure_note: str
    pcr: float | None
    oi_note: str
    global_note: str
    high_impact_events: list[str] = field(default_factory=list)
    no_trade_reason: str = ""


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.rename(columns=str.capitalize)


def _last_price(ticker: str) -> float | None:
    try:
        df = yf.Ticker(ticker).history(period="5d", interval="1m", auto_adjust=True)
        df = _normalize(df)
        if df.empty:
            df = yf.Ticker(ticker).history(period="5d", interval="5m", auto_adjust=True)
            df = _normalize(df)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def _session_change_pct(ticker: str) -> float:
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="5m", auto_adjust=True)
        df = _normalize(df)
        if len(df) < 2:
            return 0.0
        o = float(df["Open"].iloc[0])
        c = float(df["Close"].iloc[-1])
        return round((c - o) / o * 100, 2) if o else 0.0
    except Exception:
        return 0.0


def _us_futures_momentum() -> tuple[float, float, str]:
    dow = _session_change_pct("YM=F")
    if abs(dow) < 0.05:
        dow = _session_change_pct("^DJI")
    nasdaq = _session_change_pct("NQ=F")
    if abs(nasdaq) < 0.05:
        nasdaq = _session_change_pct(GLOBAL_TICKERS.get("NASDAQ", "^IXIC"))
    note = f"Dow {dow:+.2f}%, Nasdaq {nasdaq:+.2f}%"
    return dow, nasdaq, note


def _fetch_gift_price(gift_tickers: tuple[str, ...]) -> float | None:
    for ticker in gift_tickers:
        px = _last_price(ticker)
        if px and px > 0:
            return px
    return None


def _structure_analysis(ticker: str) -> tuple[float, str]:
    """Score -2..+2 from 15m close location + daily EMA20."""
    score = 0.0
    notes: list[str] = []
    try:
        df15 = fetch_index_history(ticker, "15m", period="10d")
        df15 = _normalize(df15)
        if len(df15) >= 10:
            today = now_ist().date()
            idx = df15.index.tz_convert("Asia/Kolkata")
            session = df15.loc[idx.date == today]
            if len(session) >= 3:
                hi = float(session["High"].max())
                lo = float(session["Low"].min())
                close = float(session["Close"].iloc[-1])
                rng = hi - lo
                if rng > 0:
                    pos = (close - lo) / rng
                    if pos >= 0.75:
                        score += 1.2
                        notes.append("15m closing near day's high (aggressive buyers)")
                    elif pos <= 0.25:
                        score -= 1.2
                        notes.append("15m closing near day's low (aggressive sellers)")
                    else:
                        notes.append("15m mid-range close")
        daily = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=True)
        daily = _normalize(daily)
        if len(daily) >= 25:
            c = float(daily["Close"].iloc[-1])
            o = float(daily["Open"].iloc[-1])
            e20 = float(ema(daily["Close"], 20).iloc[-1])
            if c > e20:
                score += 0.6
                notes.append("Daily close above EMA20")
            elif c < e20:
                score -= 0.6
                notes.append("Daily close below EMA20")
            if c > o:
                score += 0.4
                notes.append("Strong green daily candle")
            elif c < o:
                score -= 0.4
                notes.append("Red daily candle")
    except Exception:
        logger.debug("Structure analysis failed for %s", ticker, exc_info=True)
    return score, "; ".join(notes) if notes else "Neutral structure"


def _option_chain_metrics(upstox_key: str, spot: float) -> tuple[float | None, str]:
    if not upstox_configured():
        return None, "Option chain unavailable (no Upstox token)"
    expiries = fetch_expiries(upstox_key)
    exp = nearest_expiry(expiries)
    if not exp:
        return None, "No option expiry found"
    chain = fetch_option_chain(exp, upstox_key)
    if not chain:
        return None, "Option chain fetch failed"

    call_oi = 0.0
    put_oi = 0.0
    top_call: tuple[int, float] = (0, 0.0)
    top_put: tuple[int, float] = (0, 0.0)

    for row in chain:
        strike = int(float(row.get("strike_price") or 0))
        for leg_key, is_call in (("call_options", True), ("put_options", False)):
            leg = row.get(leg_key) or {}
            oi = float(leg.get("open_interest") or leg.get("oi") or 0)
            if oi <= 0:
                md = leg.get("market_data") or {}
                oi = float(md.get("oi") or 0)
            if is_call:
                call_oi += oi
                if oi > top_call[1]:
                    top_call = (strike, oi)
            else:
                put_oi += oi
                if oi > top_put[1]:
                    top_put = (strike, oi)

    pcr = round(put_oi / call_oi, 2) if call_oi > 0 else None
    oi_note = (
        f"PCR {pcr:.2f}" if pcr else "PCR n/a"
    )
    if top_put[0]:
        oi_note += f" · Max Put OI {top_put[0]}"
    if top_call[0]:
        oi_note += f" · Max Call OI {top_call[0]}"
    if pcr and pcr > 1.15:
        oi_note += " (put writing / support bias)"
    elif pcr and pcr < 0.85:
        oi_note += " (call writing / resistance bias)"
    return pcr, oi_note


def _normalize_probs(up: float, down: float, flat: float) -> tuple[float, float, float]:
    up = max(0.0, up)
    down = max(0.0, down)
    flat = max(0.0, flat)
    total = up + down + flat
    if total <= 0:
        return 33.0, 33.0, 34.0
    return round(up / total * 100, 0), round(down / total * 100, 0), round(flat / total * 100, 0)


def assess_gap_probability(spec: IndexBtstSpec) -> GapAssessment:
    spot = _last_price(spec.yf_ticker) or 0.0
    gift = _fetch_gift_price(spec.gift_tickers)
    gift_premium = round(gift - spot, 1) if gift and spot else None

    dow_pct, nasdaq_pct, us_note = _us_futures_momentum()
    struct_score, struct_note = _structure_analysis(spec.yf_ticker)
    pcr, oi_note = _option_chain_metrics(spec.upstox_key, spot)

    news = build_market_news_digest()
    high_impact = detect_high_impact_events(news.headlines)

    up_score = 33.0
    down_score = 33.0
    flat_score = 34.0

    if gift_premium is not None:
        if gift_premium > 15:
            up_score += 18
            down_score -= 8
        elif gift_premium > 5:
            up_score += 10
            down_score -= 4
        elif gift_premium < -15:
            down_score += 18
            up_score -= 8
        elif gift_premium < -5:
            down_score += 10
            up_score -= 4
        else:
            flat_score += 6

    up_score += max(-12, min(12, dow_pct * 4 + nasdaq_pct * 3))
    down_score += max(-12, min(12, -(dow_pct * 4 + nasdaq_pct * 3)))

    up_score += struct_score * 8
    down_score -= struct_score * 8

    if pcr:
        if pcr > 1.2:
            up_score += 8
        elif pcr < 0.8:
            down_score += 8

    if news.news_bias == "bullish":
        up_score += 5
    elif news.news_bias == "bearish":
        down_score += 5

    gap_up, gap_down, flat = _normalize_probs(up_score, down_score, flat_score)

    global_note = us_note
    if gift_premium is not None:
        tag = "premium" if gift_premium > 0 else "discount"
        global_note += f" · GIFT {tag} {abs(gift_premium):.0f} pts vs spot"

    action = "NO TRADE"
    no_trade_reason = ""
    if high_impact:
        no_trade_reason = "High-impact event tonight/tomorrow — skip overnight risk."
    elif gap_up >= INDEX_BTST_MIN_GAP_PROB and gap_up > gap_down + 5:
        action = "HOLD CALL (CE)"
    elif gap_down >= INDEX_BTST_MIN_GAP_PROB and gap_down > gap_up + 5:
        action = "HOLD PUT (PE)"
    else:
        no_trade_reason = (
            f"No clear edge (Gap Up {gap_up:.0f}% / Down {gap_down:.0f}% / Flat {flat:.0f}%)."
        )

    return GapAssessment(
        gap_up_pct=gap_up,
        gap_down_pct=gap_down,
        flat_pct=flat,
        action=action,
        spot=round(spot, 2),
        gift=round(gift, 2) if gift else None,
        gift_premium_pts=gift_premium,
        dow_pct=dow_pct,
        nasdaq_pct=nasdaq_pct,
        structure_note=struct_note,
        pcr=pcr,
        oi_note=oi_note,
        global_note=global_note,
        high_impact_events=high_impact,
        no_trade_reason=no_trade_reason,
    )


def format_btst_report(spec: IndexBtstSpec, a: GapAssessment) -> str:
    ts = now_ist().strftime("%d %b %Y, %H:%M IST")
    gift_line = (
        f"GIFT {a.gift:,.2f} ({a.gift_premium_pts:+.0f} vs spot {a.spot:,.2f})"
        if a.gift and a.gift_premium_pts is not None
        else f"Spot {a.spot:,.2f} (GIFT data unavailable — US futures weighted higher)"
    )
    pcr_line = f"PCR {a.pcr:.2f} · {a.oi_note}" if a.pcr else a.oi_note
    events = (
        "\n".join(f"• {e}" for e in a.high_impact_events)
        if a.high_impact_events
        else "None flagged"
    )
    lot = spec.lot_size
    return "\n".join(
        [
            f"📊 <b>{spec.label} BTST Gap Analysis</b> · {ts}",
            f"<b>Strategy:</b> {spec.strategy_name}",
            "",
            "<b>📊 Probability Score</b>",
            f"• <b>Gap Up:</b> {a.gap_up_pct:.0f}%",
            f"• <b>Gap Down:</b> {a.gap_down_pct:.0f}%",
            f"• <b>Flat:</b> {a.flat_pct:.0f}%",
            "",
            "<b>🔍 Technical Breakdown</b>",
            f"• <b>Global & GIFT:</b> {a.global_note} · {gift_line}",
            f"• <b>Price action:</b> {a.structure_note}",
            f"• <b>Option chain:</b> {pcr_line}",
            f"• <b>Event risk:</b> {events}",
            "",
            "<b>⚡ Execution & Risk Strategy</b>",
            f"• <b>Final action:</b> {a.action}",
            f"• <b>Lot guidance:</b> Trade only <b>{INDEX_BTST_LOT_GUIDANCE_PCT}%</b> of normal size "
            f"({lot} qty/lot on {spec.label}) — strict overnight risk control.",
            "• <b>Exit plan:</b> Square off at <b>9:15 AM IST</b> tomorrow (do not carry through the day).",
        ]
        + ([f"• <i>{a.no_trade_reason}</i>"] if a.no_trade_reason else [])
    )


def _build_option_signal(spec: IndexBtstSpec, a: GapAssessment) -> Signal | None:
    if a.spot <= 0:
        return None
    flip = "CALL" if "CALL" in a.action else "PUT"
    strike = _round_strike(a.spot, spec.strike_step)
    opt_type = "CE" if flip == "CALL" else "PE"

    quote, src = spec.fetch_quote(strike, opt_type)
    premium_source = "estimate"
    spot = a.spot
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
                expiry = _weekly_expiry_label(spec.expiry_weekday)
        else:
            expiry = _weekly_expiry_label(spec.expiry_weekday)
    else:
        expiry = _weekly_expiry_label(spec.expiry_weekday)
        df = fetch_index_history(spec.yf_ticker, "15m", period="10d")
        df = _normalize(df)
        atr_val = float(atr(df["High"], df["Low"], df["Close"], 14).iloc[-1]) if len(df) > 20 else spot * 0.005
        premium = _estimate_atm_premium(spot, atr_val)

    levels = _option_levels_points(premium)
    ref = spot * (0.995 if flip == "CALL" else 1.005)
    idx_sl, idx_target = _underlying_targets(spot, ref, flip)
    side = f"BUY {'CALL' if flip == 'CALL' else 'PUT'} BTST"

    note = format_btst_report(spec, a)
    return Signal(
        symbol=f"{spec.label} {strike} {opt_type}",
        strategy=spec.strategy_name,
        side=side,
        levels=levels,
        note=note,
        kind="ENTRY",
        timeframe="BTST",
        timestamp=now_ist().strftime("%d %b %Y, %H:%M IST"),
        instrument=spec.instrument,
        strike=float(strike),
        option_type=opt_type,
        expiry_label=expiry,
        underlying=spot,
        underlying_sl=idx_sl,
        underlying_target=idx_target,
        premium_source=premium_source,
        option_points_mode=True,
    )


def run_index_btst_alert(spec: IndexBtstSpec) -> Signal | None:
    if not is_market_open() or not is_index_btst_window():
        return None
    if spec.sent_check():
        logger.info("%s BTST already sent today.", spec.label)
        return None

    assessment = assess_gap_probability(spec)
    report = format_btst_report(spec, assessment)

    if assessment.action == "NO TRADE":
        if send_plain(report, html_mode=True):
            spec.mark_sent()
            logger.info("%s BTST NO TRADE sent.", spec.label)
        return None

    sig = _build_option_signal(spec, assessment)
    if sig is None:
        return None

    from telegram_client import send_signal
    from trade_journal import record_trade

    if send_signal(sig):
        spec.mark_sent()
        record_trade(sig)
        from position_lifecycle import register_premium_open

        register_premium_open(sig)
        logger.info("%s BTST trade alert: %s", spec.label, sig.side)
        return sig
    logger.error("%s BTST signal send failed.", spec.label)
    return None
