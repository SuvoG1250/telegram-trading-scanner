"""
Nifty index options — Supertrend flip strategy (Pine: ATR 10, factor 3).

Bullish flip (direction change < 0) → Buy Call (CE)
Bearish flip (direction change > 0) → Buy Put (PE)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from config import (
    MIN_RISK_REWARD_PLAYBOOK,
    NIFTY_OPTION_PREMIUM_ATR_FACTOR,
    NIFTY_OPTION_PREMIUM_SL_PCT,
    NIFTY_OPTIONS_ENABLED,
    NIFTY_ST_ATR_LENGTH,
    NIFTY_ST_FACTOR,
    NIFTY_ST_INTERVAL,
    NIFTY_STRIKE_STEP,
)
from data_fetcher import today_session_df
from indicators import atr, compute_supertrend, supertrend_flip_pine
from market_sentiment import NIFTY_TICKER
from market_time import is_chaitu_session, is_market_open, now_ist
from dhan_client import dhan_configured, fetch_nifty_option_quote

logger = logging.getLogger(__name__)

STRATEGY_NAME = "Nifty Supertrend Options"


def _round_strike(spot: float) -> int:
    return int(round(spot / NIFTY_STRIKE_STEP) * NIFTY_STRIKE_STEP)


def _weekly_expiry_label(dt=None) -> str:
    """Nifty weekly expiry: Tuesday (NSE)."""
    dt = dt or now_ist()
    tuesday = 1
    days = (tuesday - dt.weekday()) % 7
    if days == 0 and dt.hour >= 15 and dt.minute >= 30:
        days = 7
    expiry = dt + timedelta(days=days)
    return expiry.strftime("%d %b %Y")


def _estimate_atm_premium(spot: float, atr_val: float) -> float:
    by_atr = atr_val * NIFTY_OPTION_PREMIUM_ATR_FACTOR
    by_spot = spot * 0.0035
    return round(max(by_atr, by_spot, 40.0), 2)


def _option_levels(entry_premium: float) -> TradeLevels:
    sl_frac = NIFTY_OPTION_PREMIUM_SL_PCT / 100.0
    risk = max(entry_premium * sl_frac, 5.0)
    sl = entry_premium - risk
    t1 = entry_premium + risk * 1.0
    t2 = entry_premium + risk * MIN_RISK_REWARD_PLAYBOOK
    return TradeLevels(
        entry=round(entry_premium, 2),
        stop_loss=round(max(sl, 1.0), 2),
        target_1=round(t1, 2),
        target_2=round(t2, 2),
        best_target=round(t2, 2),
        rr_best=MIN_RISK_REWARD_PLAYBOOK,
        trailing_note="Book 70% at T1; trail runner. Exit all by 3:25 PM IST.",
        risk=round(risk, 2),
        reward_1=round(t1 - entry_premium, 2),
        reward_2=round(t2 - entry_premium, 2),
    )


def _underlying_targets(spot: float, st_line: float, flip: str) -> tuple[float, float]:
    risk_pts = max(abs(spot - st_line), spot * 0.002)
    if flip == "CALL":
        return round(st_line, 2), round(spot + risk_pts * MIN_RISK_REWARD_PLAYBOOK, 2)
    return round(st_line, 2), round(spot - risk_pts * MIN_RISK_REWARD_PLAYBOOK, 2)


def _fetch_nifty_session(interval: str) -> pd.DataFrame:
    period = "5d" if interval in ("1m", "5m", "3m") else "10d"
    raw = yf.Ticker(NIFTY_TICKER).history(period=period, interval=interval, auto_adjust=True)
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.capitalize)
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC")
    return today_session_df(raw, now_ist().date())


def scan_nifty_supertrend_option() -> Signal | None:
    if not NIFTY_OPTIONS_ENABLED:
        return None
    if not is_market_open() or not is_chaitu_session():
        return None

    interval = NIFTY_ST_INTERVAL if NIFTY_ST_INTERVAL in ("1m", "5m", "15m") else "5m"
    session = _fetch_nifty_session(interval)
    if len(session) < NIFTY_ST_ATR_LENGTH + 3:
        return None

    st = compute_supertrend(session, length=NIFTY_ST_ATR_LENGTH, multiplier=NIFTY_ST_FACTOR)
    flip = supertrend_flip_pine(st)
    if flip is None:
        return None

    spot = float(session["Close"].iloc[-1])
    st_line = float(st["st_line"].iloc[-1])
    strike = _round_strike(spot)
    opt_type = "CE" if flip == "CALL" else "PE"

    premium_source = "estimate"
    quote = fetch_nifty_option_quote(strike, opt_type) if dhan_configured() else None
    if quote:
        premium = quote.last_price
        premium_source = "dhan"
        if quote.spot:
            spot = quote.spot
        expiry_iso = quote.expiry
        expiry = datetime.strptime(quote.expiry, "%Y-%m-%d").strftime("%d %b %Y")
    else:
        expiry = _weekly_expiry_label()
        atr_val = float(
            atr(session["High"], session["Low"], session["Close"], NIFTY_ST_ATR_LENGTH).iloc[-1]
        )
        premium = _estimate_atm_premium(spot, atr_val)

    levels = _option_levels(premium)
    idx_sl, idx_target = _underlying_targets(spot, st_line, flip)

    action = "BUY CALL" if flip == "CALL" else "BUY PUT"

    return Signal(
        symbol=f"NIFTY {strike} {opt_type}",
        strategy=STRATEGY_NAME,
        side=action,
        levels=levels,
        note="",
        kind="ENTRY",
        timeframe=interval.upper(),
        timestamp=now_ist().strftime("%d %b %Y, %H:%M IST"),
        instrument="NIFTY_OPTION",
        strike=float(strike),
        option_type=opt_type,
        expiry_label=expiry,
        underlying=spot,
        underlying_sl=idx_sl,
        underlying_target=idx_target,
        premium_source=premium_source,
    )
