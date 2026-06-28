"""
Global market strategy engine — 5 setups from playbook, pick highest-score signal.

1. EMA 9/21 crossover + volume
2. VWAP pullback bounce (trend continuation)
3. Resistance/support breakout + high volume
4. Breakout retest entry (confirmation)
5. Opening Range Breakout (first 15 min, 5m bars)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from config import (
    GLOBAL_EMA_FAST,
    GLOBAL_EMA_SLOW,
    GLOBAL_ENABLE_BUY,
    GLOBAL_ENABLE_SELL,
    GLOBAL_LONDON_START_HOUR,
    GLOBAL_MIN_SIGNAL_SCORE,
    GLOBAL_NY_START_HOUR,
    GLOBAL_RR_RATIO,
    GLOBAL_VOLUME_MULT,
)
from indicators import atr, ema, session_vwap, volume_sma

logger = logging.getLogger(__name__)

StrategyFn = Callable[[pd.DataFrame, int, str], "GlobalSignal | None"]

ORB_SESSION_UTC: dict[str, list[tuple[int, int]]] = {
    "BTCUSD": [(0, 0), (13, 30)],
    "ETHUSD": [(0, 0), (13, 30)],
    "XAUUSD": [(8, 0), (13, 30)],
}


@dataclass
class GlobalSignal:
    strategy: str
    side: str
    entry: float
    stop: float
    target: float
    score: float
    analysis: str
    signal_time: str
    reasons: list[str] = field(default_factory=list)
    rr: float = GLOBAL_RR_RATIO

    @property
    def confidence(self) -> str:
        if self.score >= 85:
            return "HIGH"
        if self.score >= 72:
            return "MEDIUM"
        return "LOW"


def _round_px(px: float, symbol: str) -> float:
    if symbol == "XAUUSD":
        return round(px, 2)
    if px >= 1000:
        return round(px, 2)
    return round(px, 4)


def _bar(df: pd.DataFrame, idx: int) -> pd.Series:
    return df.iloc[idx]


def _vol_ok(df: pd.DataFrame, idx: int, mult: float | None = None) -> tuple[bool, float]:
    mult = mult or GLOBAL_VOLUME_MULT
    if "Volume" not in df.columns:
        return True, 1.0
    vol = float(df["Volume"].iloc[idx])
    avg = float(volume_sma(df["Volume"], 20).iloc[idx])
    if avg <= 0:
        return True, 1.0
    ratio = vol / avg
    return ratio >= mult, ratio


def _risk_levels(
    side: str,
    entry: float,
    bar_low: float,
    bar_high: float,
    ref: float,
    atr_val: float,
    symbol: str,
    *,
    rr: float | None = None,
) -> tuple[float, float] | None:
    rr = rr or GLOBAL_RR_RATIO
    buffer = max(atr_val * 0.12, entry * 0.0003)
    if side == "BUY":
        stop = _round_px(min(bar_low, ref) - buffer, symbol)
        risk = entry - stop
        if risk <= 0:
            return None
        return stop, _round_px(entry + risk * rr, symbol)
    stop = _round_px(max(bar_high, ref) + buffer, symbol)
    risk = stop - entry
    if risk <= 0:
        return None
    return stop, _round_px(entry - risk * rr, symbol)


def _make_signal(
    *,
    strategy: str,
    side: str,
    df: pd.DataFrame,
    idx: int,
    symbol: str,
    base_score: float,
    ref: float,
    analysis: str,
    reasons: list[str],
    vol_ratio: float = 1.0,
    rr: float | None = None,
) -> GlobalSignal | None:
    if side == "BUY" and not GLOBAL_ENABLE_BUY:
        return None
    if side == "SELL" and not GLOBAL_ENABLE_SELL:
        return None

    bar = _bar(df, idx)
    entry = _round_px(float(bar["Close"]), symbol)
    atr_val = float(atr(df["High"], df["Low"], df["Close"], 14).iloc[idx])
    levels = _risk_levels(
        side,
        entry,
        float(bar["Low"]),
        float(bar["High"]),
        ref,
        atr_val,
        symbol,
        rr=rr,
    )
    if not levels:
        return None
    stop, target = levels

    score = base_score
    if vol_ratio >= GLOBAL_VOLUME_MULT:
        score += 10
    elif vol_ratio >= GLOBAL_VOLUME_MULT * 0.85:
        score += 4
    if side == "BUY" and float(bar["Close"]) > float(bar["Open"]):
        score += 3
    if side == "SELL" and float(bar["Close"]) < float(bar["Open"]):
        score += 3

    ts = df.index[idx].isoformat()
    return GlobalSignal(
        strategy=strategy,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        score=min(99.0, score),
        analysis=analysis,
        signal_time=ts,
        reasons=reasons,
        rr=rr or GLOBAL_RR_RATIO,
    )


def detect_ema_crossover(df: pd.DataFrame, idx: int, symbol: str) -> GlobalSignal | None:
    """9 EMA crosses 21 EMA with volume confirmation."""
    if idx < 22:
        return None
    close = df["Close"]
    e9 = ema(close, GLOBAL_EMA_FAST)
    e21 = ema(close, GLOBAL_EMA_SLOW)
    prev_f, prev_s = float(e9.iloc[idx - 1]), float(e21.iloc[idx - 1])
    cur_f, cur_s = float(e9.iloc[idx]), float(e21.iloc[idx])
    vol_ok, vol_ratio = _vol_ok(df, idx)

    if cur_f > cur_s and prev_f <= prev_s:
        side = "BUY"
        analysis = f"EMA {GLOBAL_EMA_FAST} crossed above EMA {GLOBAL_EMA_SLOW} · vol {vol_ratio:.1f}x avg"
    elif cur_f < cur_s and prev_f >= prev_s:
        side = "SELL"
        analysis = f"EMA {GLOBAL_EMA_FAST} crossed below EMA {GLOBAL_EMA_SLOW} · vol {vol_ratio:.1f}x avg"
    else:
        return None

    if not vol_ok:
        return None

    reasons = ["EMA crossover", f"volume {vol_ratio:.1f}x"]
    return _make_signal(
        strategy="EMA Crossover + Volume",
        side=side,
        df=df,
        idx=idx,
        symbol=symbol,
        base_score=72.0,
        ref=float(e21.iloc[idx]),
        analysis=analysis,
        reasons=reasons,
        vol_ratio=vol_ratio,
    )


def detect_vwap_pullback(df: pd.DataFrame, idx: int, symbol: str) -> GlobalSignal | None:
    """Trend + pullback to VWAP + bounce candle with volume."""
    if idx < 25:
        return None
    vwap = session_vwap(df)
    close = df["Close"]
    bar = _bar(df, idx)
    v = float(vwap.iloc[idx])
    vol_ok, vol_ratio = _vol_ok(df, idx, mult=GLOBAL_VOLUME_MULT * 0.9)

    # Uptrend: price mostly above VWAP last 8 bars, touched VWAP on this bar, bullish close
    above_count = sum(float(close.iloc[idx - k]) > float(vwap.iloc[idx - k]) for k in range(1, 9))
    touched = float(bar["Low"]) <= v * 1.0015 and float(bar["Close"]) >= v * 0.999
    bullish = float(bar["Close"]) > float(bar["Open"])

    if above_count >= 5 and touched and bullish and vol_ok:
        analysis = f"VWAP pullback bounce · trend up · vol {vol_ratio:.1f}x · VWAP {v:.2f}"
        return _make_signal(
            strategy="VWAP Pullback",
            side="BUY",
            df=df,
            idx=idx,
            symbol=symbol,
            base_score=76.0,
            ref=v,
            analysis=analysis,
            reasons=["VWAP support bounce", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
        )

    below_count = sum(float(close.iloc[idx - k]) < float(vwap.iloc[idx - k]) for k in range(1, 9))
    touched_res = float(bar["High"]) >= v * 0.9985 and float(bar["Close"]) <= v * 1.001
    bearish = float(bar["Close"]) < float(bar["Open"])

    if below_count >= 5 and touched_res and bearish and vol_ok:
        analysis = f"VWAP pullback rejection · trend down · vol {vol_ratio:.1f}x · VWAP {v:.2f}"
        return _make_signal(
            strategy="VWAP Pullback",
            side="SELL",
            df=df,
            idx=idx,
            symbol=symbol,
            base_score=76.0,
            ref=v,
            analysis=analysis,
            reasons=["VWAP resistance rejection", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
        )
    return None


def detect_breakout_volume(df: pd.DataFrame, idx: int, symbol: str) -> GlobalSignal | None:
    """Close breaks recent range with high volume."""
    lookback = 48
    if idx < lookback + 3:
        return None
    window = df.iloc[idx - lookback : idx]
    resistance = float(window["High"].max())
    support = float(window["Low"].min())
    close = float(df["Close"].iloc[idx])
    vol_ok, vol_ratio = _vol_ok(df, idx)

    if close > resistance and vol_ok:
        analysis = f"Breakout above {resistance:.2f} · high volume {vol_ratio:.1f}x"
        return _make_signal(
            strategy="Breakout + Volume",
            side="BUY",
            df=df,
            idx=idx,
            symbol=symbol,
            base_score=80.0,
            ref=resistance,
            analysis=analysis,
            reasons=["resistance breakout", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
        )

    if close < support and vol_ok:
        analysis = f"Breakdown below {support:.2f} · high volume {vol_ratio:.1f}x"
        return _make_signal(
            strategy="Breakout + Volume",
            side="SELL",
            df=df,
            idx=idx,
            symbol=symbol,
            base_score=80.0,
            ref=support,
            analysis=analysis,
            reasons=["support breakdown", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
        )
    return None


def detect_retest_entry(df: pd.DataFrame, idx: int, symbol: str) -> GlobalSignal | None:
    """Breakout then retest of level as support/resistance."""
    lookback = 48
    if idx < lookback + 8:
        return None

    window = df.iloc[idx - lookback : idx - 6]
    if window.empty:
        return None
    level_high = float(window["High"].max())
    level_low = float(window["Low"].min())
    tol = level_high * 0.003

    # Bullish: breakout in bars idx-12..idx-4, retest near level_high now
    broke_out = False
    for j in range(idx - 12, idx - 3):
        if j < 1:
            continue
        if float(df["Close"].iloc[j]) > level_high and float(df["Close"].iloc[j - 1]) <= level_high:
            broke_out = True
            break
    bar = _bar(df, idx)
    near = abs(float(bar["Low"]) - level_high) <= tol or abs(float(bar["Close"]) - level_high) <= tol
    bullish = float(bar["Close"]) > float(bar["Open"]) and float(bar["Close"]) > level_high * 0.998
    vol_ok, vol_ratio = _vol_ok(df, idx, mult=GLOBAL_VOLUME_MULT * 0.85)

    if broke_out and near and bullish and vol_ok:
        analysis = f"Retest entry · broke {level_high:.2f} · held as support · vol {vol_ratio:.1f}x"
        return _make_signal(
            strategy="Retest Entry",
            side="BUY",
            df=df,
            idx=idx,
            symbol=symbol,
            base_score=86.0,
            ref=level_high,
            analysis=analysis,
            reasons=["breakout retest", "support hold", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
            rr=max(GLOBAL_RR_RATIO, 3.0),
        )

    broke_down = False
    for j in range(idx - 12, idx - 3):
        if j < 1:
            continue
        if float(df["Close"].iloc[j]) < level_low and float(df["Close"].iloc[j - 1]) >= level_low:
            broke_down = True
            break
    near_res = abs(float(bar["High"]) - level_low) <= tol
    bearish = float(bar["Close"]) < float(bar["Open"]) and float(bar["Close"]) < level_low * 1.002

    if broke_down and near_res and bearish and vol_ok:
        analysis = f"Retest entry · broke {level_low:.2f} · held as resistance · vol {vol_ratio:.1f}x"
        return _make_signal(
            strategy="Retest Entry",
            side="SELL",
            df=df,
            idx=idx,
            symbol=symbol,
            base_score=86.0,
            ref=level_low,
            analysis=analysis,
            reasons=["breakdown retest", "resistance hold", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
            rr=max(GLOBAL_RR_RATIO, 3.0),
        )
    return None


def _find_orb_range(df5: pd.DataFrame, symbol: str, idx: int) -> tuple[float, float] | None:
    """First 15 minutes (3×5m bars) after latest session open before bar idx."""
    if idx < 5:
        return None
    ts = df5.index[idx]
    utc = ts.tz_convert("UTC")
    sessions = ORB_SESSION_UTC.get(symbol, [(GLOBAL_LONDON_START_HOUR, 0), (GLOBAL_NY_START_HOUR, 30)])

    best_open = None
    for hour, minute in sessions:
        candidate = utc.normalize() + pd.Timedelta(hours=hour, minutes=minute)
        if candidate > utc:
            candidate -= pd.Timedelta(days=1)
        if best_open is None or candidate > best_open:
            best_open = candidate

    if best_open is None:
        return None

    orb_end = best_open + pd.Timedelta(minutes=15)
    orb_bars = df5[(df5.index >= best_open) & (df5.index < orb_end)]
    if len(orb_bars) < 2:
        return None
    if df5.index[idx] <= orb_end:
        return None
    return float(orb_bars["High"].max()), float(orb_bars["Low"].min())


def detect_orb_breakout(df5: pd.DataFrame, idx: int, symbol: str) -> GlobalSignal | None:
    """Opening range breakout — buy above OR high, sell below OR low."""
    rng = _find_orb_range(df5, symbol, idx)
    if not rng:
        return None
    or_high, or_low = rng
    close = float(df5["Close"].iloc[idx])
    vol_ok, vol_ratio = _vol_ok(df5, idx)

    if close > or_high and vol_ok:
        analysis = f"ORB buy above {or_high:.2f} (15m range) · vol {vol_ratio:.1f}x"
        return _make_signal(
            strategy="Opening Range Breakout",
            side="BUY",
            df=df5,
            idx=idx,
            symbol=symbol,
            base_score=78.0,
            ref=or_high,
            analysis=analysis,
            reasons=["ORB high break", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
        )

    if close < or_low and vol_ok:
        analysis = f"ORB sell below {or_low:.2f} (15m range) · vol {vol_ratio:.1f}x"
        return _make_signal(
            strategy="Opening Range Breakout",
            side="SELL",
            df=df5,
            idx=idx,
            symbol=symbol,
            base_score=78.0,
            ref=or_low,
            analysis=analysis,
            reasons=["ORB low break", f"vol {vol_ratio:.1f}x"],
            vol_ratio=vol_ratio,
        )
    return None


ALL_STRATEGIES_15M: list[tuple[str, StrategyFn]] = [
    ("ema", detect_ema_crossover),
    ("vwap", detect_vwap_pullback),
    ("breakout", detect_breakout_volume),
    ("retest", detect_retest_entry),
]


def scan_bar_15m(df: pd.DataFrame, idx: int, symbol: str) -> list[GlobalSignal]:
    signals: list[GlobalSignal] = []
    for _name, fn in ALL_STRATEGIES_15M:
        try:
            sig = fn(df, idx, symbol)
            if sig:
                signals.append(sig)
        except Exception:
            logger.debug("Strategy %s failed at idx %s", _name, idx, exc_info=True)
    return signals


def pick_best_signal(signals: list[GlobalSignal]) -> GlobalSignal | None:
    """Highest score wins; confluence bonus when multiple strategies agree."""
    if not signals:
        return None

    by_side: dict[str, list[GlobalSignal]] = {}
    for s in signals:
        by_side.setdefault(s.side, []).append(s)

    best: GlobalSignal | None = None
    for side, group in by_side.items():
        top = max(group, key=lambda x: x.score)
        bonus = min(12, (len(group) - 1) * 6)
        names = sorted({g.strategy for g in group})
        merged_score = min(99.0, top.score + bonus)
        merged_reasons = list(top.reasons)
        if bonus > 0:
            merged_reasons.append(f"confluence ×{len(group)} ({', '.join(names)})")
        candidate = GlobalSignal(
            strategy=top.strategy if len(group) == 1 else f"Best of {len(group)} ({top.strategy})",
            side=side,
            entry=top.entry,
            stop=top.stop,
            target=top.target,
            score=merged_score,
            analysis=top.analysis + (f" · Confluence: {', '.join(names)}" if bonus else ""),
            signal_time=top.signal_time,
            reasons=merged_reasons,
            rr=top.rr,
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def find_best_global_signal(
    df15: pd.DataFrame,
    df5: pd.DataFrame | None,
    symbol: str,
    *,
    lookback: int,
) -> GlobalSignal | None:
    """Scan recent closed bars across all strategies and return the best setup."""
    if df15 is None or len(df15) < 30:
        return None

    candidates: list[GlobalSignal] = []
    for offset in range(2, 2 + max(1, lookback)):
        idx = len(df15) - offset
        candidates.extend(scan_bar_15m(df15, idx, symbol))

    if df5 is not None and len(df5) >= 20:
        for offset in range(2, min(6, lookback)):
            idx = len(df5) - offset
            sig = detect_orb_breakout(df5, idx, symbol)
            if sig:
                candidates.append(sig)

    best = pick_best_signal(candidates)
    if best and best.score >= GLOBAL_MIN_SIGNAL_SCORE:
        return best
    if best:
        logger.info(
            "Global %s — best %s %s score %.1f below min %.1f",
            symbol,
            best.strategy,
            best.side,
            best.score,
            GLOBAL_MIN_SIGNAL_SCORE,
        )
    return None
