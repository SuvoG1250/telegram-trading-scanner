"""
Index options — SuperTrend flip → Buy CE/PE (Nifty, Sensex).

Shared engine for NIFTY_ST_ENGINE (tv / exit490) and premium point plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
import yfinance as yf

from config import (
    MIN_RISK_REWARD_PLAYBOOK,
    MIN_TARGET_PROFIT_PCT,
    NIFTY_EXIT490_ATR_BARS,
    NIFTY_EXIT490_ATR_MULT,
    NIFTY_OPTION_PREMIUM_ATR_FACTOR,
    NIFTY_OPTION_PREMIUM_SL_POINTS,
    NIFTY_OPTION_PREMIUM_SL_PCT,
    NIFTY_OPTION_PREMIUM_TARGET_POINTS,
    NIFTY_OPTION_PREMIUM_TRAIL_MAX_POINTS,
    NIFTY_ST_ATR_LENGTH,
    NIFTY_ST_ENGINE,
    NIFTY_ST_FACTOR,
    NIFTY_ST_INTERVAL,
)
from data_fetcher import today_session_df
from indicators import atr, compute_supertrend, compute_supertrend_exit490, supertrend_flip_pine
from market_time import is_chaitu_session, is_market_open, now_ist
from risk import TradeLevels
from telegram_client import Signal

logger = logging.getLogger(__name__)

QuoteFn = Callable[[int, str], tuple[object | None, str]]


@dataclass(frozen=True)
class IndexOptionSpec:
    key: str
    label: str
    strategy_name: str
    instrument: str
    yf_ticker: str
    strike_step: int
    expiry_weekday: int
    enabled: bool
    fetch_quote: QuoteFn


def _round_strike(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


def _weekly_expiry_label(expiry_weekday: int, dt=None) -> str:
    dt = dt or now_ist()
    days = (expiry_weekday - dt.weekday()) % 7
    if days == 0 and dt.hour >= 15 and dt.minute >= 30:
        days = 7
    expiry = dt + timedelta(days=days)
    return expiry.strftime("%d %b %Y")


def _estimate_atm_premium(spot: float, atr_val: float) -> float:
    by_atr = atr_val * NIFTY_OPTION_PREMIUM_ATR_FACTOR
    by_spot = spot * 0.0035
    return round(max(by_atr, by_spot, 40.0), 2)


def _option_levels_percent(entry_premium: float) -> TradeLevels:
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


def _option_levels_points(entry_premium: float) -> TradeLevels:
    sl_pts = NIFTY_OPTION_PREMIUM_SL_POINTS
    t1_pts = NIFTY_OPTION_PREMIUM_TARGET_POINTS
    max_pts = NIFTY_OPTION_PREMIUM_TRAIL_MAX_POINTS
    risk = max(sl_pts, 1.0)
    sl = max(entry_premium - sl_pts, 0.5)
    t1 = entry_premium + t1_pts
    best = entry_premium + max_pts
    rr = (best - entry_premium) / risk if risk > 0 else 0.0
    return TradeLevels(
        entry=round(entry_premium, 2),
        stop_loss=round(sl, 2),
        target_1=round(t1, 2),
        target_2=round(best, 2),
        best_target=round(best, 2),
        rr_best=round(rr, 2),
        trailing_note=(
            f"Book ~70% at +₹{t1_pts:.0f}; trail runner toward +₹{max_pts:.0f} from entry; "
            f"initial SL −₹{sl_pts:.0f} on premium (tighten manually). Exit by 3:25 PM IST."
        ),
        risk=round(risk, 2),
        reward_1=round(t1_pts, 2),
        reward_2=round(max_pts, 2),
    )


def _underlying_targets(spot: float, st_line: float, flip: str) -> tuple[float, float]:
    risk_pts = max(abs(spot - st_line), spot * 0.002)
    if flip == "CALL":
        return round(st_line, 2), round(spot + risk_pts * MIN_RISK_REWARD_PLAYBOOK, 2)
    return round(st_line, 2), round(spot - risk_pts * MIN_RISK_REWARD_PLAYBOOK, 2)


def _normalize_yf_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.capitalize)
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC")
    return raw


def _fetch_index_history(ticker: str, interval: str) -> pd.DataFrame:
    period = "5d" if interval in ("1m", "5m", "3m") else "10d"
    raw = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    return _normalize_yf_history(raw)


def _fetch_index_session(ticker: str, interval: str) -> pd.DataFrame:
    raw = _fetch_index_history(ticker, interval)
    if raw.empty:
        return raw
    return today_session_df(raw, now_ist().date())


def scan_index_supertrend_option(spec: IndexOptionSpec) -> Signal | None:
    if not spec.enabled:
        return None
    if not is_market_open() or not is_chaitu_session():
        return None

    interval = NIFTY_ST_INTERVAL if NIFTY_ST_INTERVAL in ("1m", "5m", "15m") else "5m"
    session = _fetch_index_session(spec.yf_ticker, interval)
    min_len = max(NIFTY_ST_ATR_LENGTH, NIFTY_EXIT490_ATR_BARS, 3) + 3
    if len(session) < min_len:
        return None

    if NIFTY_ST_ENGINE == "exit490":
        st_raw = compute_supertrend_exit490(
            session,
            bars_back=NIFTY_EXIT490_ATR_BARS,
            mult=NIFTY_EXIT490_ATR_MULT,
        )
        st_flip = st_raw.assign(direction=-st_raw["direction"])
        flip = supertrend_flip_pine(st_flip)
    else:
        st_raw = compute_supertrend(session, length=NIFTY_ST_ATR_LENGTH, multiplier=NIFTY_ST_FACTOR)
        flip = supertrend_flip_pine(st_raw)

    if flip is None:
        return None

    spot = float(session["Close"].iloc[-1])
    st_line = float(st_raw["st_line"].iloc[-1])
    return build_index_option_signal(
        spec,
        flip=flip,
        session=session,
        interval=interval,
        spot=spot,
        ref_line=st_line,
    )


def build_index_option_signal(
    spec: IndexOptionSpec,
    *,
    flip: str,
    session: pd.DataFrame,
    interval: str,
    spot: float,
    ref_line: float,
    note: str = "",
) -> Signal | None:
    strike = _round_strike(spot, spec.strike_step)
    opt_type = "CE" if flip == "CALL" else "PE"

    premium_source = "estimate"
    quote, src = spec.fetch_quote(strike, opt_type)
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
        logger.info(
            "%s option LTP source=%s strike=%s %s premium=%.2f",
            spec.label,
            src,
            strike,
            opt_type,
            premium,
        )
    else:
        expiry = _weekly_expiry_label(spec.expiry_weekday)
        atr_len = NIFTY_ST_ATR_LENGTH if NIFTY_ST_ENGINE != "exit490" else max(NIFTY_EXIT490_ATR_BARS, 1)
        atr_val = float(
            atr(session["High"], session["Low"], session["Close"], atr_len).iloc[-1]
        )
        premium = _estimate_atm_premium(spot, atr_val)

    use_points = NIFTY_OPTION_PREMIUM_SL_POINTS > 0
    levels = _option_levels_points(premium) if use_points else _option_levels_percent(premium)
    if not use_points:
        profit_pct = levels.target_profit_pct("BUY")
        if profit_pct < MIN_TARGET_PROFIT_PCT:
            logger.info(
                "Skip %s option — premium target %.1f%% below min %.1f%%.",
                spec.label,
                profit_pct,
                MIN_TARGET_PROFIT_PCT,
            )
            return None

    idx_sl, idx_target = _underlying_targets(spot, ref_line, flip)
    action = "BUY CALL" if flip == "CALL" else "BUY PUT"

    return Signal(
        symbol=f"{spec.label} {strike} {opt_type}",
        strategy=spec.strategy_name,
        side=action,
        levels=levels,
        note=note,
        kind="ENTRY",
        timeframe=interval.upper(),
        timestamp=now_ist().strftime("%d %b %Y, %H:%M IST"),
        instrument=spec.instrument,
        strike=float(strike),
        option_type=opt_type,
        expiry_label=expiry,
        underlying=spot,
        underlying_sl=idx_sl,
        underlying_target=idx_target,
        premium_source=premium_source,
        option_points_mode=use_points,
    )
