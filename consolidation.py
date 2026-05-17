"""
Sector SuperTrend + 4H consolidation breakout on 3-minute chart.

Stock selection: Nifty 100, quality filter, strong sector (daily ST), consolidating in uptrend.
Entry: 1st 3m close above 4H resistance; 2nd 3m breaks high of 1st.
SL: prior day low, or 1st 3m low if risk too wide.
Exit alert: 3m close below 9 EMA (trailing).
"""

from __future__ import annotations

import logging

import pandas as pd

from config import (
    CONSOLIDATION_RANGE_PCT,
    MAX_SL_RISK_PCT,
    SECTOR_ST_MIN_BULLISH_PCT,
    SUPERTREND_LENGTH,
    SUPERTREND_MULTIPLIER,
)
from data_fetcher import fetch_daily, fetch_history, fetch_intraday, today_session_df
from indicators import ema, supertrend_direction
from market_time import now_ist
from sector_map import sector_for, symbols_by_sector
from stock_quality import passes_quality_filter

logger = logging.getLogger(__name__)


def fetch_4h(symbol: str) -> pd.DataFrame:
    df = fetch_history(symbol, "1h", period="60d")
    if df.empty:
        return df
    import pytz

    ist = pytz.timezone("Asia/Kolkata")
    local = df.index.tz_convert(ist)
    df = df.copy()
    df.index = local
    return df.resample("4h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])


def _daily_supertrend_bullish(symbol: str) -> bool:
    daily = fetch_daily(symbol, period="6mo")
    if len(daily) < 30:
        return False
    direction = supertrend_direction(
        daily, length=SUPERTREND_LENGTH, multiplier=SUPERTREND_MULTIPLIER
    )
    return float(direction.iloc[-1]) > 0


def strong_sectors() -> set[str]:
    """Sectors with majority of stocks on daily SuperTrend green."""
    strong: set[str] = set()
    for sector, symbols in symbols_by_sector().items():
        if sector == "Other" or len(symbols) < 2:
            continue
        bullish = sum(1 for s in symbols[:12] if _daily_supertrend_bullish(s))
        checked = min(len(symbols), 12)
        if checked and bullish / checked >= SECTOR_ST_MIN_BULLISH_PCT:
            strong.add(sector)
    return strong


def consolidation_levels(symbol: str) -> dict | None:
    """4H support/resistance and consolidation flag."""
    df4 = fetch_4h(symbol)
    if len(df4) < 15:
        return None
    window = df4.tail(20)
    resistance = float(window["High"].max())
    support = float(window["Low"].min())
    close = float(window["Close"].iloc[-1])
    if close <= 0:
        return None
    range_pct = ((resistance - support) / close) * 100
    mid = (resistance + support) / 2
    in_zone = support <= close <= resistance
    near_mid = abs(close - mid) / close * 100 < (range_pct / 2)

    if range_pct > CONSOLIDATION_RANGE_PCT or not in_zone:
        return None
    if not _daily_supertrend_bullish(symbol):
        return None

    return {
        "resistance": resistance,
        "support": support,
        "range_pct": round(range_pct, 2),
        "consolidating": in_zone and near_mid,
    }


def is_consolidation_candidate(symbol: str, strong: set[str] | None = None) -> dict | None:
    ok, metrics = passes_quality_filter(symbol)
    if not ok:
        return None
    sector = sector_for(symbol)
    strong = strong or strong_sectors()
    if sector not in strong:
        return None
    levels = consolidation_levels(symbol)
    if not levels or not levels.get("consolidating"):
        return None
    return {**metrics, **levels, "sector": sector}


def _prev_day_last_low(symbol: str) -> float | None:
    daily = fetch_daily(symbol, period="1mo")
    if len(daily) < 2:
        return None
    yday = daily.iloc[-2]
    return float(yday["Low"])


def _stop_loss_long(symbol: str, entry: float, first_3m_low: float) -> float:
    primary = _prev_day_last_low(symbol)
    if primary is None:
        return first_3m_low
    risk_pct = ((entry - primary) / entry) * 100 if entry else 99
    if risk_pct > MAX_SL_RISK_PCT:
        return min(first_3m_low, primary)
    return primary


def first_two_3m_candles(session_3m: pd.DataFrame) -> tuple[pd.Series, pd.Series] | None:
    day = session_3m.between_time("09:15", "09:30")
    if len(day) < 2:
        day = session_3m.head(2)
    if len(day) < 2:
        return None
    return day.iloc[0], day.iloc[1]


def check_3m_breakout_entry(symbol: str, levels: dict) -> dict | None:
    df3 = fetch_intraday(symbol, "3m")
    session = today_session_df(df3, now_ist().date())
    candles = first_two_3m_candles(session)
    if candles is None:
        return None

    first, second = candles
    resistance = levels["resistance"]
    c1 = float(first["Close"])
    h1 = float(first["High"])
    h2 = float(second["High"])

    if c1 <= resistance:
        return None
    if h2 <= h1:
        return None

    entry = float(second["Close"])
    first_low = float(first["Low"])
    sl = _stop_loss_long(symbol, entry, first_low)
    if entry <= sl:
        return None

    return {
        "entry": entry,
        "stop_loss": sl,
        "resistance": resistance,
        "support": levels["support"],
        "first_3m_high": h1,
    }


def check_9ema_trail_exit(symbol: str) -> dict | None:
    """Exit when 3m candle closes below 9 EMA after being above it."""
    df3 = fetch_intraday(symbol, "3m")
    session = today_session_df(df3, now_ist().date())
    if len(session) < 12:
        return None

    session = session.copy()
    session["EMA9"] = ema(session["Close"], 9)
    prev = session.iloc[-2]
    last = session.iloc[-1]
    if float(prev["Close"]) <= float(prev["EMA9"]):
        return None
    if float(last["Close"]) >= float(last["EMA9"]):
        return None
    return {"exit_price": float(last["Close"]), "ema9": float(last["EMA9"])}
