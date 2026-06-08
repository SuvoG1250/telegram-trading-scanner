"""Market data retrieval via yfinance (throttled + retries for GitHub Actions)."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Literal

import pandas as pd
import yfinance as yf

from stocks import to_yfinance_symbol

logger = logging.getLogger(__name__)

Interval = Literal["1m", "3m", "5m", "15m", "1h", "1d", "1wk", "1mo"]

_PERIOD_BY_INTERVAL: dict[str, str] = {
    "1m": "5d",
    "3m": "5d",
    "5m": "5d",
    "15m": "10d",
    "1h": "60d",
    "1d": "1y",
    "1wk": "2y",
    "1mo": "5y",
}

_YF_LOCK = threading.Lock()
_LAST_YF_FETCH = 0.0
_YF_MIN_INTERVAL_SEC = float(os.environ.get("YFINANCE_MIN_INTERVAL_SEC", "0.45"))
_YF_MAX_RETRIES = max(1, int(os.environ.get("YFINANCE_MAX_RETRIES", "4")))


def _throttle_yfinance() -> None:
    global _LAST_YF_FETCH
    with _YF_LOCK:
        elapsed = time.time() - _LAST_YF_FETCH
        if elapsed < _YF_MIN_INTERVAL_SEC:
            time.sleep(_YF_MIN_INTERVAL_SEC - elapsed)
        _LAST_YF_FETCH = time.time()


def _is_rate_limited(exc: BaseException) -> bool:
    name = type(exc).__name__
    if "RateLimit" in name or "Too Many Requests" in str(exc):
        return True
    return False


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.capitalize)
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    df = df.dropna(subset=["Close"])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def fetch_history(
    symbol: str,
    interval: Interval,
    period: str | None = None,
) -> pd.DataFrame:
    period = period or _PERIOD_BY_INTERVAL.get(interval, "60d")
    ticker = to_yfinance_symbol(symbol)
    last_err: Exception | None = None

    for attempt in range(_YF_MAX_RETRIES):
        _throttle_yfinance()
        try:
            df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
            return _normalize_df(df)
        except Exception as exc:
            last_err = exc
            if _is_rate_limited(exc):
                wait = min(30.0, 2.0 * (2**attempt))
                logger.warning(
                    "yfinance rate limit for %s (%s) — retry %s/%s in %.0fs",
                    symbol,
                    interval,
                    attempt + 1,
                    _YF_MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue
            logger.exception("Failed to fetch %s (%s)", symbol, interval)
            return pd.DataFrame()

    if last_err is not None:
        logger.error("yfinance gave up on %s (%s): %s", symbol, interval, last_err)
    return pd.DataFrame()


def fetch_daily(symbol: str, period: str = "1y") -> pd.DataFrame:
    return fetch_history(symbol, "1d", period=period)


def fetch_intraday(symbol: str, interval: Interval) -> pd.DataFrame:
    return fetch_history(symbol, interval)


def today_session_df(df: pd.DataFrame, session_date) -> pd.DataFrame:
    """Filter intraday bars to a single IST calendar day."""
    if df.empty:
        return df
    import pytz

    ist = pytz.timezone("Asia/Kolkata")
    local_index = df.index.tz_convert(ist)
    mask = local_index.date == session_date
    out = df.loc[mask].copy()
    out.index = local_index[mask]
    return out


_SESSION_CACHE: dict[tuple[str, str], pd.DataFrame] = {}


def clear_session_cache() -> None:
    _SESSION_CACHE.clear()


def get_today_session(symbol: str, interval: Interval) -> pd.DataFrame:
    """Cached intraday bars for today's IST session (one fetch per symbol per scan run)."""
    key = (symbol, interval)
    if key not in _SESSION_CACHE:
        df = fetch_intraday(symbol, interval)
        from market_time import now_ist

        _SESSION_CACHE[key] = today_session_df(df, now_ist().date())
    return _SESSION_CACHE[key]
