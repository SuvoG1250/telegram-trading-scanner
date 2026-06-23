"""
Index options — EMA 9/21 crossover + MACD histogram + candle confirmation on 3m.

Chart setup (TradingView — see strategies/ema_macd_9_21_crossover.pine):
  • Regular OHLC candles, 3-minute
  • EMA 9 / EMA 21 on close
  • MACD fast=34, slow=144, signal=9

CE (Call):
  • EMA 9 crosses above EMA 21 on closed bar
  • Candle is green (Close > Open) with strong bullish body (not doji)
  • MACD histogram bar is green (hist > 0)
  • SuperTrend / HMA trend filter aligned (configurable)

PE (Put):
  • EMA 9 crosses below EMA 21 on closed bar
  • Candle is red (Close < Open)
  • MACD histogram bar is red (hist < 0)
  • SuperTrend / HMA trend filter aligned (configurable)
"""

from __future__ import annotations

import logging

import pandas as pd

from config import (
    EMA_MACD_EMA_FAST,
    EMA_MACD_EMA_SLOW,
    EMA_MACD_FAST_LENGTH,
    EMA_MACD_HMA_LENGTH,
    EMA_MACD_INTERVAL,
    EMA_MACD_MAX_DOJI_BODY_RATIO,
    EMA_MACD_MIN_BULL_BODY_RATIO,
    EMA_MACD_OPTIONS_ENABLED,
    EMA_MACD_SIGNAL_LENGTH,
    EMA_MACD_SLOW_LENGTH,
    EMA_MACD_ST_ATR_BARS,
    EMA_MACD_ST_ATR_MULT,
    EMA_MACD_TREND_FILTER,
    EMA_MACD_USE_HEIKIN,
    NIFTY_OPTIONS_ENABLED,
    NIFTY_STRIKE_STEP,
    SENSEX_OPTIONS_ENABLED,
    SENSEX_STRIKE_STEP,
    SENSEX_TICKER,
)
from data_fetcher import fetch_index_history, resample_ohlcv
from index_options import IndexOptionSpec, build_index_option_signal
from indicators import compute_macd, compute_supertrend_exit490, ema, heikin_ashi, hma
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


def _price_bars(raw: pd.DataFrame) -> pd.DataFrame:
    """Bars used for EMA/MACD (optionally Heikin Ashi). Candle color always uses raw OHLC."""
    if EMA_MACD_USE_HEIKIN:
        return heikin_ashi(raw)
    return raw


def _red_candle(bar: pd.Series) -> bool:
    return float(bar["Close"]) < float(bar["Open"])


def _green_candle_strong(raw_bar: pd.Series) -> bool:
    o, h, low, c = (
        float(raw_bar["Open"]),
        float(raw_bar["High"]),
        float(raw_bar["Low"]),
        float(raw_bar["Close"]),
    )
    if c <= o:
        return False
    rng = max(h - low, 1e-9)
    body = c - o
    body_ratio = body / rng
    if body_ratio < EMA_MACD_MIN_BULL_BODY_RATIO:
        return False
    if body_ratio <= EMA_MACD_MAX_DOJI_BODY_RATIO:
        return False
    return True


def _trend_ok(side: str, raw: pd.DataFrame, idx: int) -> bool:
    """SuperTrend and/or HMA alignment on the signal bar."""
    filt = EMA_MACD_TREND_FILTER
    if filt in ("", "none", "off", "false", "0"):
        return True

    st_ok = True
    hma_ok = True

    if filt in ("supertrend", "both", "st"):
        st = compute_supertrend_exit490(
            raw,
            bars_back=EMA_MACD_ST_ATR_BARS,
            mult=EMA_MACD_ST_ATR_MULT,
        )
        direction = float(st["direction"].iloc[idx])
        # exit490: +1 long/green, -1 short/red
        st_ok = direction > 0 if side == "CALL" else direction < 0

    if filt in ("hma", "both"):
        hma_line = float(hma(raw["Close"], EMA_MACD_HMA_LENGTH).iloc[idx])
        close = float(raw["Close"].iloc[idx])
        hma_ok = close > hma_line if side == "CALL" else close < hma_line

    if filt == "both":
        return st_ok and hma_ok
    if filt in ("supertrend", "st"):
        return st_ok
    if filt == "hma":
        return hma_ok
    # unknown filter — default supertrend only
    return st_ok


def _ema_macd_signal(raw: pd.DataFrame, price: pd.DataFrame, idx: int = -2) -> str | None:
    """
    Strict CE/PE rules on closed bar idx (default -2).
    Candle color read from raw OHLC; EMA/MACD from price series.
    """
    pi = len(price) + idx if idx < 0 else idx
    if pi < 2 or pi >= len(price):
        return None

    close = price["Close"]
    e_fast = ema(close, EMA_MACD_EMA_FAST)
    e_slow = ema(close, EMA_MACD_EMA_SLOW)
    hist = compute_macd(
        close,
        fast=EMA_MACD_FAST_LENGTH,
        slow=EMA_MACD_SLOW_LENGTH,
        signal=EMA_MACD_SIGNAL_LENGTH,
    )["histogram"]

    cur_hist = float(hist.iloc[pi])
    raw_bar = raw.iloc[pi]

    bull_cross = (
        float(e_fast.iloc[pi - 1]) <= float(e_slow.iloc[pi - 1])
        and float(e_fast.iloc[pi]) > float(e_slow.iloc[pi])
    )
    bear_cross = (
        float(e_fast.iloc[pi - 1]) >= float(e_slow.iloc[pi - 1])
        and float(e_fast.iloc[pi]) < float(e_slow.iloc[pi])
    )

    if bull_cross and _green_candle_strong(raw_bar) and cur_hist > 0:
        if _trend_ok("CALL", raw, pi):
            return "CALL"
    if bear_cross and _red_candle(raw_bar) and cur_hist < 0:
        if _trend_ok("PUT", raw, pi):
            return "PUT"
    return None


def _ema_macd_flip(raw: pd.DataFrame, price: pd.DataFrame) -> str | None:
    return _ema_macd_signal(raw, price, idx=-2)


def _signal_note(flip: str, ema_slow: float, hist: float, interval: str, *, filter_tag: str) -> str:
    candle = "Heikin Ashi" if EMA_MACD_USE_HEIKIN else "OHLC"
    if flip == "CALL":
        cross = "EMA 9 crossed above EMA 21"
        confirm = "green candle + MACD hist green"
    else:
        cross = "EMA 9 crossed below EMA 21"
        confirm = "red candle + MACD hist red"
    return (
        f"{cross} · {confirm} (hist {hist:.2f}) "
        f"({EMA_MACD_FAST_LENGTH}/{EMA_MACD_SLOW_LENGTH}/{EMA_MACD_SIGNAL_LENGTH}) · "
        f"{candle} {interval} · EMA21 ₹{ema_slow:,.2f} · filter: {filter_tag}"
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

    price = _price_bars(raw)
    flip = _ema_macd_flip(raw, price)
    if flip is None:
        return None

    closed_bar = raw.index[-2]
    closed_ist = closed_bar.tz_convert("Asia/Kolkata")
    if closed_ist.date() != now_ist().date():
        logger.debug("%s EMA+MACD — cross not on today's session.", spec.label)
        return None

    spot = float(raw["Close"].iloc[-1])
    ema_slow = float(ema(price["Close"], EMA_MACD_EMA_SLOW).iloc[-2])
    hist = float(
        compute_macd(
            price["Close"],
            fast=EMA_MACD_FAST_LENGTH,
            slow=EMA_MACD_SLOW_LENGTH,
            signal=EMA_MACD_SIGNAL_LENGTH,
        )["histogram"].iloc[-2]
    )
    filter_tag = EMA_MACD_TREND_FILTER or "none"
    note = _signal_note(flip, ema_slow, hist, interval, filter_tag=filter_tag)

    logger.info(
        "%s EMA+MACD %s — spot=%.2f hist=%.2f ema21=%.2f bars=%d filter=%s",
        spec.label,
        flip,
        spot,
        hist,
        ema_slow,
        len(raw),
        filter_tag,
    )

    return build_index_option_signal(
        spec,
        flip=flip,
        session=raw,
        interval=interval,
        spot=spot,
        ref_line=ema_slow,
        note=note,
    )


def scan_nifty_ema_macd_option() -> Signal | None:
    return scan_index_ema_macd_option(NIFTY_EMA_MACD_SPEC)


def scan_sensex_ema_macd_option() -> Signal | None:
    return scan_index_ema_macd_option(SENSEX_EMA_MACD_SPEC)
