"""Technical indicators (EMA, ATR, Supertrend) — pure pandas, no paid APIs."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin Ashi OHLC (TradingView-style) from standard OHLC."""
    o = df["Open"].astype(float)
    h = df["High"].astype(float)
    low = df["Low"].astype(float)
    c = df["Close"].astype(float)
    ha_close = (o + h + low + c) / 4.0
    ha_open = pd.Series(index=df.index, dtype=float)
    ha_open.iloc[0] = (o.iloc[0] + c.iloc[0]) / 2.0
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0
    ha_high = pd.concat([h, ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([low, ha_open, ha_close], axis=1).min(axis=1)
    out = df.copy()
    out["Open"] = ha_open
    out["High"] = ha_high
    out["Low"] = ha_low
    out["Close"] = ha_close
    return out


def hma(series: pd.Series, length: int) -> pd.Series:
    """Hull Moving Average (TradingView ta.hma)."""
    length = max(int(length), 2)
    half = max(length // 2, 1)
    sqrt_len = max(int(length**0.5), 1)

    def _wma(s: pd.Series, n: int) -> pd.Series:
        n = max(n, 1)
        weights = pd.Series(range(1, n + 1), dtype=float)

        def _apply(x: pd.Series) -> float:
            w = weights.iloc[-len(x) :]
            return float((x * w).sum() / w.sum())

        return s.rolling(n).apply(_apply, raw=False)

    raw = 2 * _wma(series, half) - _wma(series, length)
    return _wma(raw, sqrt_len)


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram (macd − signal)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "histogram": histogram},
        index=close.index,
    )


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def volume_sma(volume: pd.Series, length: int = 20) -> pd.Series:
    return volume.rolling(length, min_periods=max(1, length // 2)).mean()


def session_vwap(df: pd.DataFrame, tz: str = "UTC") -> pd.Series:
    """Session VWAP reset each calendar day in `tz`."""
    typical = (df["High"].astype(float) + df["Low"].astype(float) + df["Close"].astype(float)) / 3.0
    vol = df["Volume"].astype(float).fillna(0) if "Volume" in df.columns else pd.Series(1.0, index=df.index)
    if float(vol.sum()) <= 0:
        return typical.expanding(min_periods=1).mean()
    day_key = df.index.tz_convert(tz).normalize()
    pv = typical * vol
    cum_pv = pv.groupby(day_key).cumsum()
    cum_vol = vol.groupby(day_key).cumsum().replace(0, 1e-10)
    return cum_pv / cum_vol


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def supertrend_direction(
    df: pd.DataFrame,
    length: int = 7,
    multiplier: float = 3.0,
) -> pd.Series:
    """
    Returns direction series: +1 bullish (green), -1 bearish (red).
    Matches common Supertrend(7, 3) used in intraday systems.
    """
    hl2 = (df["High"] + df["Low"]) / 2
    atr_vals = atr(df["High"], df["Low"], df["Close"], length)
    basic_ub = hl2 + multiplier * atr_vals
    basic_lb = hl2 - multiplier * atr_vals

    final_ub = basic_ub.copy()
    final_lb = basic_lb.copy()
    for i in range(1, len(df)):
        if basic_ub.iloc[i] < final_ub.iloc[i - 1] or df["Close"].iloc[i - 1] > final_ub.iloc[i - 1]:
            final_ub.iloc[i] = basic_ub.iloc[i]
        else:
            final_ub.iloc[i] = final_ub.iloc[i - 1]
        if basic_lb.iloc[i] > final_lb.iloc[i - 1] or df["Close"].iloc[i - 1] < final_lb.iloc[i - 1]:
            final_lb.iloc[i] = basic_lb.iloc[i]
        else:
            final_lb.iloc[i] = final_lb.iloc[i - 1]

    direction = pd.Series(1, index=df.index, dtype=float)
    st = pd.Series(index=df.index, dtype=float)
    st.iloc[0] = final_ub.iloc[0]
    for i in range(1, len(df)):
        if st.iloc[i - 1] == final_ub.iloc[i - 1]:
            if df["Close"].iloc[i] <= final_ub.iloc[i]:
                st.iloc[i] = final_ub.iloc[i]
                direction.iloc[i] = -1
            else:
                st.iloc[i] = final_lb.iloc[i]
                direction.iloc[i] = 1
        else:
            if df["Close"].iloc[i] >= final_lb.iloc[i]:
                st.iloc[i] = final_lb.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i] = final_ub.iloc[i]
                direction.iloc[i] = -1
    return direction


def compute_supertrend(
    df: pd.DataFrame,
    length: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """
    Supertrend matching TradingView ta.supertrend(factor, atrPeriod).

    Returns DataFrame columns:
      st_line   — active supertrend value
      direction — -1 uptrend (bullish), +1 downtrend (bearish) [Pine convention]
    """
    hl2 = (df["High"] + df["Low"]) / 2
    atr_vals = atr(df["High"], df["Low"], df["Close"], length)
    basic_ub = hl2 + multiplier * atr_vals
    basic_lb = hl2 - multiplier * atr_vals

    final_ub = basic_ub.copy()
    final_lb = basic_lb.copy()
    for i in range(1, len(df)):
        if basic_ub.iloc[i] < final_ub.iloc[i - 1] or df["Close"].iloc[i - 1] > final_ub.iloc[i - 1]:
            final_ub.iloc[i] = basic_ub.iloc[i]
        else:
            final_ub.iloc[i] = final_ub.iloc[i - 1]
        if basic_lb.iloc[i] > final_lb.iloc[i - 1] or df["Close"].iloc[i - 1] < final_lb.iloc[i - 1]:
            final_lb.iloc[i] = final_lb.iloc[i]
        else:
            final_lb.iloc[i] = final_lb.iloc[i - 1]

    st_line = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=float)
    st_line.iloc[0] = final_ub.iloc[0]
    direction.iloc[0] = 1.0

    for i in range(1, len(df)):
        prev_st = st_line.iloc[i - 1]
        if prev_st == final_ub.iloc[i - 1]:
            if df["Close"].iloc[i] > final_ub.iloc[i]:
                st_line.iloc[i] = final_lb.iloc[i]
                direction.iloc[i] = -1.0
            else:
                st_line.iloc[i] = final_ub.iloc[i]
                direction.iloc[i] = 1.0
        else:
            if df["Close"].iloc[i] < final_lb.iloc[i]:
                st_line.iloc[i] = final_ub.iloc[i]
                direction.iloc[i] = 1.0
            else:
                st_line.iloc[i] = final_lb.iloc[i]
                direction.iloc[i] = -1.0

    return pd.DataFrame({"st_line": st_line, "direction": direction}, index=df.index)


def compute_supertrend_exit490(
    df: pd.DataFrame,
    bars_back: int = 1,
    mult: float = 3.0,
) -> pd.DataFrame:
    """
    SuperTrend ATR with trailing bands (exit490 / Mauricio Pimenta Pine v4 style).

    direction: +1 long (green / longStop), -1 short (red / shortStop).
    st_line: active stop line value (valueToPlot in Pine).
    """
    hl2 = (df["High"] + df["Low"]) / 2.0
    atr_vals = atr(df["High"], df["Low"], df["Close"], max(int(bars_back), 1)) * float(mult)
    long_stop = hl2 - atr_vals
    short_stop = hl2 + atr_vals

    ls = long_stop.copy()
    ss = short_stop.copy()
    for i in range(1, len(df)):
        lp = float(ls.iloc[i - 1])
        if float(df["Close"].iloc[i - 1]) > lp:
            ls.iloc[i] = max(float(ls.iloc[i]), lp)
        sp = float(ss.iloc[i - 1])
        if float(df["Close"].iloc[i - 1]) < sp:
            ss.iloc[i] = min(float(ss.iloc[i]), sp)

    direction = pd.Series(1.0, index=df.index, dtype=float)
    for i in range(1, len(df)):
        d = float(direction.iloc[i - 1])
        lp = float(ls.iloc[i - 1])
        sp = float(ss.iloc[i - 1])
        c = float(df["Close"].iloc[i])
        if d == -1.0 and c > sp:
            direction.iloc[i] = 1.0
        elif d == 1.0 and c < lp:
            direction.iloc[i] = -1.0
        else:
            direction.iloc[i] = d

    line = pd.Series(index=df.index, dtype=float)
    for i in range(len(df)):
        line.iloc[i] = float(ls.iloc[i]) if float(direction.iloc[i]) == 1.0 else float(ss.iloc[i])

    return pd.DataFrame({"st_line": line, "direction": direction}, index=df.index)


def supertrend_flip_pine(st: pd.DataFrame) -> str | None:
    """
    Pine: change(direction) < 0 → long (Buy Call); change(direction) > 0 → short (Buy Put).
    Returns 'CALL', 'PUT', or None if no flip on last bar.
    """
    if len(st) < 2:
        return None
    prev_dir = float(st["direction"].iloc[-2])
    curr_dir = float(st["direction"].iloc[-1])
    change = curr_dir - prev_dir
    if change < 0:
        return "CALL"
    if change > 0:
        return "PUT"
    return None


def supertrend_flip_closed_bar(st: pd.DataFrame) -> str | None:
    """Flip on last closed bar (-3 vs -2) — matches bar-close alerts."""
    if len(st) < 3:
        return None
    prev_dir = float(st["direction"].iloc[-3])
    curr_dir = float(st["direction"].iloc[-2])
    change = curr_dir - prev_dir
    if change < 0:
        return "CALL"
    if change > 0:
        return "PUT"
    return None
