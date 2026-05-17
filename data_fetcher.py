"""Market data retrieval via yfinance."""

from __future__ import annotations

import logging
from typing import Literal

import pandas as pd
import yfinance as yf

from stocks import to_yfinance_symbol

logger = logging.getLogger(__name__)

Interval = Literal["5m", "15m", "1h", "1d", "1wk", "1mo"]

_PERIOD_BY_INTERVAL: dict[str, str] = {
    "5m": "5d",
    "15m": "10d",
    "1h": "60d",
    "1d": "1y",
    "1wk": "2y",
    "1mo": "5y",
}


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
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    except Exception:
        logger.exception("Failed to fetch %s (%s)", symbol, interval)
        return pd.DataFrame()
    return _normalize_df(df)


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
