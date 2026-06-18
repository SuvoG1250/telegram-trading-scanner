"""
Index options — EMA 9/21 crossover + simultaneous MACD histogram color flip.

Chart setup (TradingView):
  • Heikin Ashi candles, 3-minute
  • EMA 9 / EMA 21 on price
  • MACD fast=34, slow=144, signal=9, source=close

Signals (options only):
  • CALL: EMA 9 crosses above 21 AND MACD histogram turns green on same bar
  • PUT:  EMA 9 crosses below 21 AND MACD histogram turns red on same bar
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
from data_fetcher import fetch_index_history, resample_ohlcv
from index_options import IndexOptionSpec, build_index_option_signal
from indicators import compute_macd, ema, heikin_ashi
from market_sentiment import NIFTY_TICKER
from market_time import is_chaitu_session, is_market_open, now_ist
from option_quotes import fetch_nifty_option_quote, fetch_sensex_option_quote
from telegram_client import Signal

logger = logging.getLogger(__name__)

STRATEGY_NAME = "EMA 9/21 + MACD Sync Options"

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


def _load_ema_macd_bars(ticker: str) -> tuple[pd.DataFrame, str]:
    """yfinance has no native 3m — resample 1m bars to 3-minute OHLC."""
    target = EMA_MACD_INTERVAL if EMA_MACD_INTERVAL in ("1m", "3m", "5m", "15m") else "3m"
    if target == "3m":
        raw = fetch_index_history(ticker, "1m", period="7d")
        if raw.empty:
            return raw, "3m"
        return resample_ohlcv(raw, "3min"), "3m"
    period = "10d" if target in ("1m", "5m") else "60d"
    return fetch_index_history(ticker, target, period=period), target


def _prepare_bars(raw: pd.DataFrame) -> pd.DataFrame:
    if EMA_MACD_USE_HEIKIN:
        return heikin_ashi(raw)
    return raw


def _ema_macd_flip(df: pd.DataFrame) -> str | None:
    """
    EMA 9/21 cross on last closed bar (-2 vs -3) AND MACD histogram color flip on same bar.
    CALL: cross above + hist turns green (<=0 to >0).
    PUT:  cross below + hist turns red (>=0 to <0).
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
    prev_hist = float(prev["MACD_Hist"])
    cur_hist = float(cur["MACD_Hist"])

    bull_cross = prev["EMA_Fast"] <= prev["EMA_Slow"] and cur["EMA_Fast"] > cur["EMA_Slow"]
    bear_cross = prev["EMA_Fast"] >= prev["EMA_Slow"] and cur["EMA_Fast"] < cur["EMA_Slow"]

    turned_green = cur_hist > 0 and prev_hist <= 0
    turned_red = cur_hist < 0 and prev_hist >= 0

    if bull_cross and turned_green:
        return "CALL"
    if bear_cross and turned_red:
        return "PUT"
    return None


def _signal_note(flip: str, ema_slow: float, hist: float, prev_hist: float, interval: str) -> str:
    candle = "Heikin Ashi" if EMA_MACD_USE_HEIKIN else "OHLC"
    if flip == "CALL":
        cross = "EMA 9 crossed above EMA 21"
        macd_tag = "histogram turned green"
    else:
        cross = "EMA 9 crossed below EMA 21"
        macd_tag = "histogram turned red"
    return (
        f"{cross} · MACD {macd_tag} ({prev_hist:.2f}→{hist:.2f}) "
        f"({EMA_MACD_FAST_LENGTH}/{EMA_MACD_SLOW_LENGTH}/{EMA_MACD_SIGNAL_LENGTH}) · "
        f"{candle} {interval} · EMA21 ref ₹{ema_slow:,.2f}"
    )


def scan_index_ema_macd_option(spec: IndexOptionSpec) -> Signal | None:
    if not spec.enabled:
        return None
    if not is_market_open() or not is_chaitu_session():
        return None

    raw, interval = _load_ema_macd_bars(spec.yf_ticker)
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
        logger.debug("%s EMA+MACD — cross not on today's session.", spec.label)
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
    prev_hist = float(
        compute_macd(
            bars["Close"],
            fast=EMA_MACD_FAST_LENGTH,
            slow=EMA_MACD_SLOW_LENGTH,
            signal=EMA_MACD_SIGNAL_LENGTH,
        )["histogram"].iloc[-3]
    )
    note = _signal_note(flip, ema_slow, hist, prev_hist, interval)

    logger.info(
        "%s EMA+MACD %s — spot=%.2f hist=%.2f ema21=%.2f bars=%d",
        spec.label,
        flip,
        spot,
        hist,
        ema_slow,
        len(bars),
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
