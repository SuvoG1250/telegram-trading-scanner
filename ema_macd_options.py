"""
Index options — EMA 9/21 crossover + MACD histogram confirmation.

Chart setup (TradingView):
  • Heikin Ashi candles, 3-minute
  • EMA 9 / EMA 21 on price
  • MACD fast=34, slow=144, signal=9, source=close

Signals (options only):
  • MACD histogram green → only BUY CALL on EMA 9 cross above 21
  • MACD histogram red   → only BUY PUT  on EMA 9 cross below 21
"""

from __future__ import annotations

import logging

import pandas as pd

from config import (
    EMA_MACD_EMA_FAST,
    EMA_MACD_EMA_SLOW,
    EMA_MACD_FAST_LENGTH,
    EMA_MACD_INTERVAL,
    EMA_MACD_OPTIONS_ENABLED,
    EMA_MACD_SIGNAL_LENGTH,
    EMA_MACD_SLOW_LENGTH,
    EMA_MACD_USE_HEIKIN,
    NIFTY_OPTIONS_ENABLED,
    NIFTY_STRIKE_STEP,
    SENSEX_OPTIONS_ENABLED,
    SENSEX_STRIKE_STEP,
    SENSEX_TICKER,
)
from index_options import IndexOptionSpec, _fetch_index_history, build_index_option_signal
from indicators import compute_macd, ema, heikin_ashi
from market_sentiment import NIFTY_TICKER
from market_time import is_chaitu_session, is_market_open, now_ist
from option_quotes import fetch_nifty_option_quote, fetch_sensex_option_quote
from telegram_client import Signal

logger = logging.getLogger(__name__)

STRATEGY_NAME = "EMA 9/21 + MACD Options"

NIFTY_EMA_MACD_SPEC = IndexOptionSpec(
    key="nifty_ema_macd",
    label="NIFTY",
    strategy_name=STRATEGY_NAME,
    instrument="NIFTY_OPTION",
    yf_ticker=NIFTY_TICKER,
    strike_step=NIFTY_STRIKE_STEP,
    expiry_weekday=1,
    enabled=NIFTY_OPTIONS_ENABLED and EMA_MACD_OPTIONS_ENABLED,
    fetch_quote=lambda strike, opt: fetch_nifty_option_quote(strike, opt),
)

SENSEX_EMA_MACD_SPEC = IndexOptionSpec(
    key="sensex_ema_macd",
    label="SENSEX",
    strategy_name=STRATEGY_NAME,
    instrument="SENSEX_OPTION",
    yf_ticker=SENSEX_TICKER,
    strike_step=SENSEX_STRIKE_STEP,
    expiry_weekday=3,
    enabled=SENSEX_OPTIONS_ENABLED and EMA_MACD_OPTIONS_ENABLED,
    fetch_quote=lambda strike, opt: fetch_sensex_option_quote(strike, opt),
)


def _prepare_bars(raw: pd.DataFrame) -> pd.DataFrame:
    if EMA_MACD_USE_HEIKIN:
        return heikin_ashi(raw)
    return raw


def _ema_macd_flip(df: pd.DataFrame) -> str | None:
    """
    Detect EMA cross on last closed bar (-2 vs -3) with MACD histogram regime on -2.
    Returns 'CALL', 'PUT', or None.
    """
    if len(df) < 4:
        return None

    close = df["Close"]
    df = df.copy()
    df["EMA_Fast"] = ema(close, EMA_MACD_EMA_FAST)
    df["EMA_Slow"] = ema(close, EMA_MACD_EMA_SLOW)
    macd = compute_macd(
        close,
        fast=EMA_MACD_FAST_LENGTH,
        slow=EMA_MACD_SLOW_LENGTH,
        signal=EMA_MACD_SIGNAL_LENGTH,
    )
    df["MACD_Hist"] = macd["histogram"]

    prev = df.iloc[-3]
    cur = df.iloc[-2]
    hist = float(cur["MACD_Hist"])

    bull_cross = prev["EMA_Fast"] <= prev["EMA_Slow"] and cur["EMA_Fast"] > cur["EMA_Slow"]
    bear_cross = prev["EMA_Fast"] >= prev["EMA_Slow"] and cur["EMA_Fast"] < cur["EMA_Slow"]

    if bull_cross and hist > 0:
        return "CALL"
    if bear_cross and hist < 0:
        return "PUT"
    return None


def _signal_note(flip: str, ema_slow: float, hist: float) -> str:
    candle = "Heikin Ashi" if EMA_MACD_USE_HEIKIN else "OHLC"
    macd_tag = "green" if hist > 0 else "red"
    cross = "EMA 9 crossed above EMA 21" if flip == "CALL" else "EMA 9 crossed below EMA 21"
    return (
        f"{cross} · MACD hist {macd_tag} "
        f"({EMA_MACD_FAST_LENGTH}/{EMA_MACD_SLOW_LENGTH}/{EMA_MACD_SIGNAL_LENGTH}) · "
        f"{candle} {EMA_MACD_INTERVAL} · EMA21 ref ₹{ema_slow:,.2f}"
    )


def scan_index_ema_macd_option(spec: IndexOptionSpec) -> Signal | None:
    if not spec.enabled:
        return None
    if not is_market_open() or not is_chaitu_session():
        return None

    interval = EMA_MACD_INTERVAL if EMA_MACD_INTERVAL in ("1m", "3m", "5m", "15m") else "3m"
    raw = _fetch_index_history(spec.yf_ticker, interval)
    min_len = max(EMA_MACD_SLOW_LENGTH, EMA_MACD_EMA_SLOW, EMA_MACD_SIGNAL_LENGTH) + 5
    if len(raw) < min_len:
        logger.info(
            "%s EMA+MACD — need %d bars, have %d (%s).",
            spec.label,
            min_len,
            len(raw),
            interval,
        )
        return None

    bars = _prepare_bars(raw)
    flip = _ema_macd_flip(bars)
    if flip is None:
        return None

    closed_bar = bars.index[-2]
    closed_ist = closed_bar.tz_convert("Asia/Kolkata")
    if closed_ist.date() != now_ist().date():
        return None

    spot = float(bars["Close"].iloc[-1])
    ema_slow = float(ema(bars["Close"], EMA_MACD_EMA_SLOW).iloc[-2])
    hist = float(
        compute_macd(
            bars["Close"],
            fast=EMA_MACD_FAST_LENGTH,
            slow=EMA_MACD_SLOW_LENGTH,
            signal=EMA_MACD_SIGNAL_LENGTH,
        )["histogram"].iloc[-2]
    )
    note = _signal_note(flip, ema_slow, hist)

    logger.info(
        "%s EMA+MACD %s — spot=%.2f hist=%.2f ema21=%.2f",
        spec.label,
        flip,
        spot,
        hist,
        ema_slow,
    )

    return build_index_option_signal(
        spec,
        flip=flip,
        session=bars,
        interval=interval,
        spot=spot,
        ref_line=ema_slow,
        note=note,
    )


def scan_nifty_ema_macd_option() -> Signal | None:
    return scan_index_ema_macd_option(NIFTY_EMA_MACD_SPEC)


def scan_sensex_ema_macd_option() -> Signal | None:
    return scan_index_ema_macd_option(SENSEX_EMA_MACD_SPEC)
