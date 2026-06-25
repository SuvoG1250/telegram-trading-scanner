"""
Index options — EMA 9/21 crossover + MACD histogram + candle confirmation on 3m.

Chart setup (TradingView — see strategies/ema_macd_9_21_crossover.pine):
  • Regular OHLC candles, 3-minute (Upstox native 3m when token available)
  • EMA 9 / EMA 21 on close
  • MACD fast=34, slow=144, signal=9

CE (Call):
  • EMA 9 crosses above EMA 21 on closed bar
  • Candle is green (Close > Open) with strong bullish body (not doji)
  • MACD histogram bar is green (hist > 0)

PE (Put):
  • EMA 9 crosses below EMA 21 on closed bar
  • Candle is red (Close < Open)
  • MACD histogram bar is red (hist < 0)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from config import (
    DATA_DIR,
    EMA_MACD_EMA_FAST,
    EMA_MACD_EMA_SLOW,
    EMA_MACD_FAST_LENGTH,
    EMA_MACD_HMA_LENGTH,
    EMA_MACD_INTERVAL,
    EMA_MACD_LOOKBACK_BARS,
    EMA_MACD_MAX_DOJI_BODY_RATIO,
    EMA_MACD_MIN_BULL_BODY_RATIO,
    EMA_MACD_OPTIONS_ENABLED,
    EMA_MACD_SIGNAL_LENGTH,
    EMA_MACD_SLOW_LENGTH,
    EMA_MACD_ST_ATR_BARS,
    EMA_MACD_ST_ATR_MULT,
    EMA_MACD_TREND_FILTER,
    EMA_MACD_USE_HEIKIN,
    EMA_MACD_USE_UPSTOX_CANDLES,
    NIFTY_OPTIONS_ENABLED,
    NIFTY_STRIKE_STEP,
    SENSEX_OPTIONS_ENABLED,
    SENSEX_STRIKE_STEP,
    SENSEX_TICKER,
    UPSTOX_NIFTY_INSTRUMENT_KEY,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
)
from data_fetcher import fetch_index_history, resample_ohlcv_ist_session
from index_options import IndexOptionSpec, build_index_option_signal
from indicators import compute_macd, compute_supertrend_exit490, ema, heikin_ashi, hma
from market_sentiment import NIFTY_TICKER
from market_time import is_chaitu_session, is_market_open, now_ist, today_key
from option_quotes import fetch_nifty_option_quote, fetch_sensex_option_quote
from telegram_client import Signal

logger = logging.getLogger(__name__)

STRATEGY_NAME = "EMA 9/21 + MACD Sync Options"

_ALERT_FILE = DATA_DIR / "ema_macd_alerted_bars.json"

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

_INSTRUMENT_KEYS = {
    "nifty_ema_macd": UPSTOX_NIFTY_INSTRUMENT_KEY,
    "sensex_ema_macd": UPSTOX_SENSEX_INSTRUMENT_KEY,
}


def _load_alerted() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _ALERT_FILE.exists():
        return {"date": today_key(), "bars": {}}
    try:
        data = json.loads(_ALERT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": today_key(), "bars": {}}
    if data.get("date") != today_key():
        return {"date": today_key(), "bars": {}}
    data.setdefault("bars", {})
    return data


def _save_alerted(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data["date"] = today_key()
    _ALERT_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _alert_key(spec_key: str, bar_ts: pd.Timestamp, side: str) -> str:
    return f"{spec_key}|{side}|{bar_ts.isoformat()}"


def _bar_already_alerted(spec_key: str, bar_ts: pd.Timestamp, side: str) -> bool:
    data = _load_alerted()
    return _alert_key(spec_key, bar_ts, side) in data.get("bars", {})


def _mark_bar_alerted(spec_key: str, bar_ts: pd.Timestamp, side: str) -> None:
    data = _load_alerted()
    data.setdefault("bars", {})[_alert_key(spec_key, bar_ts, side)] = side
    _save_alerted(data)


def _load_ema_macd_bars(spec: IndexOptionSpec) -> tuple[pd.DataFrame, str, str]:
    """
    Load 3m OHLC. Prefer Upstox native 3m (matches TradingView); fallback yfinance 1m → 3m IST.
    Returns (dataframe, interval label, source tag).
    """
    interval_min = 3
    if EMA_MACD_USE_UPSTOX_CANDLES:
        from upstox_api import fetch_intraday_ohlc_df, upstox_configured

        inst = _INSTRUMENT_KEYS.get(spec.key, "")
        if upstox_configured() and inst:
            df = fetch_intraday_ohlc_df(inst, interval_minutes=interval_min)
            if len(df) >= 30:
                logger.debug("%s EMA+MACD bars from Upstox 3m (%d)", spec.label, len(df))
                return df, "3m", "upstox"

    target = EMA_MACD_INTERVAL if EMA_MACD_INTERVAL in ("1m", "3m", "5m", "15m") else "3m"
    if target == "3m":
        raw = fetch_index_history(spec.yf_ticker, "1m", period="7d")
        if raw.empty:
            return raw, "3m", "yfinance"
        return resample_ohlcv_ist_session(raw, "3min"), "3m", "yfinance_resample"
    period = "10d" if target in ("1m", "5m") else "60d"
    df = fetch_index_history(spec.yf_ticker, target, period=period)
    return df, target, "yfinance"


def _price_bars(raw: pd.DataFrame) -> pd.DataFrame:
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
    return st_ok


def _ema_macd_signal(raw: pd.DataFrame, price: pd.DataFrame, idx: int) -> str | None:
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


def _find_recent_signal(
    raw: pd.DataFrame,
    price: pd.DataFrame,
    spec_key: str,
) -> tuple[str | None, int | None]:
    """Scan last N closed bars; skip bars already alerted today."""
    lookback = max(1, EMA_MACD_LOOKBACK_BARS)
    for off in range(2, 2 + lookback):
        idx = -off
        flip = _ema_macd_signal(raw, price, idx)
        if not flip:
            continue
        pi = len(raw) + idx
        bar_ts = raw.index[pi]
        side = "BUY CALL" if flip == "CALL" else "BUY PUT"
        if _bar_already_alerted(spec_key, bar_ts, side):
            logger.debug("%s EMA+MACD skip bar %s — already alerted", spec_key, bar_ts)
            continue
        return flip, pi
    return None, None


def _signal_note(flip: str, ema_slow: float, hist: float, interval: str, *, filter_tag: str, source: str) -> str:
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
        f"{candle} {interval} · EMA21 ₹{ema_slow:,.2f} · data: {source}"
    )


def scan_index_ema_macd_option(spec: IndexOptionSpec) -> Signal | None:
    if not spec.enabled:
        return None
    if not is_market_open() or not is_chaitu_session():
        return None

    raw, interval, source = _load_ema_macd_bars(spec)
    min_len = max(EMA_MACD_SLOW_LENGTH, EMA_MACD_EMA_SLOW, EMA_MACD_SIGNAL_LENGTH) + 5
    if len(raw) < min_len:
        logger.info(
            "%s EMA+MACD — need %d bars, have %d (%s, %s).",
            spec.label,
            min_len,
            len(raw),
            interval,
            source,
        )
        return None

    price = _price_bars(raw)
    flip, pi = _find_recent_signal(raw, price, spec.key)
    if flip is None or pi is None:
        return None

    closed_bar = raw.index[pi]
    closed_ist = closed_bar.tz_convert("Asia/Kolkata") if closed_bar.tzinfo else closed_bar
    if closed_ist.date() != now_ist().date():
        logger.debug("%s EMA+MACD — signal bar not today (%s).", spec.label, closed_ist)
        return None

    spot = float(raw["Close"].iloc[-1])
    ema_slow = float(ema(price["Close"], EMA_MACD_EMA_SLOW).iloc[pi])
    hist = float(
        compute_macd(
            price["Close"],
            fast=EMA_MACD_FAST_LENGTH,
            slow=EMA_MACD_SLOW_LENGTH,
            signal=EMA_MACD_SIGNAL_LENGTH,
        )["histogram"].iloc[pi]
    )
    filter_tag = EMA_MACD_TREND_FILTER or "none"
    note = _signal_note(flip, ema_slow, hist, interval, filter_tag=filter_tag, source=source)

    logger.info(
        "%s EMA+MACD %s @ %s — spot=%.2f hist=%.2f ema21=%.2f bars=%d src=%s",
        spec.label,
        flip,
        closed_ist.strftime("%H:%M"),
        spot,
        hist,
        ema_slow,
        len(raw),
        source,
    )

    side = "BUY CALL" if flip == "CALL" else "BUY PUT"
    _mark_bar_alerted(spec.key, closed_bar, side)

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
