"""Technical indicators (EMA, ATR, Supertrend) — pure pandas, no paid APIs."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


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
