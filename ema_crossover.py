"""EMA crossover on 5m — volume momentum filter (9/15 and 9/21)."""

from __future__ import annotations

import pandas as pd

from config import EMA_FAST, EMA_SLOW, EMA_VOLUME_MULTIPLIER
from data_fetcher import fetch_history


def add_emas(df: pd.DataFrame, fast: int = EMA_FAST, slow: int = EMA_SLOW) -> pd.DataFrame:
    out = df.copy()
    out["EMA_Fast"] = out["Close"].ewm(span=fast, adjust=False).mean()
    out["EMA_Slow"] = out["Close"].ewm(span=slow, adjust=False).mean()
    return out


def crossover_signal(df: pd.DataFrame) -> str | None:
    """BUY / SELL on last closed bar vs previous (Pine-style crossover)."""
    if len(df) < 3:
        return None
    prev = df.iloc[-2]
    cur = df.iloc[-1]
    if prev["EMA_Fast"] <= prev["EMA_Slow"] and cur["EMA_Fast"] > cur["EMA_Slow"]:
        return "BUY"
    if prev["EMA_Fast"] >= prev["EMA_Slow"] and cur["EMA_Fast"] < cur["EMA_Slow"]:
        return "SELL"
    return None


def session_volume_spike(session: pd.DataFrame, multiplier: float = EMA_VOLUME_MULTIPLIER) -> bool:
    """Current bar volume vs session average (intraday momentum)."""
    if session.empty or len(session) < 5 or "Volume" not in session.columns:
        return False
    avg = float(session["Volume"].iloc[:-1].mean())
    if avg <= 0:
        return True
    current = float(session["Volume"].iloc[-1])
    return current >= avg * multiplier


def multi_day_volume_spike(symbol: str, multiplier: float = EMA_VOLUME_MULTIPLIER) -> bool:
    """Current 5m volume vs 5-day average on same interval (user script logic)."""
    hist = fetch_history(symbol, "5m", period="5d")
    if hist.empty or len(hist) < 21:
        return session_volume_spike(
            hist if not hist.empty else pd.DataFrame(),
            multiplier,
        )
    avg = float(hist["Volume"].mean())
    current = float(hist["Volume"].iloc[-1])
    if avg <= 0:
        return True
    return current >= avg * multiplier


def position_quantity(entry: float, stop_loss: float, risk_inr: float) -> int:
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share <= 0:
        return 0
    return max(int(risk_inr / risk_per_share), 0)
