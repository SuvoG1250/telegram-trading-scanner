"""
Global assets (BTC / ETH / XAU) — H4 bias + M30 fractal sweep & engulfing.

Strategy:
  • HTF H4 — EMA20/50 trend bias
  • Entry M30 — liquidity sweep of fractal high/low + engulfing confirmation
  • SL — beyond engulfing candle extreme
  • TP — fixed 1:2 R:R (configurable)
  • Sessions — London or New York (UTC hours)
  • One active plan per symbol until SL/target
"""

from __future__ import annotations

import html
import logging

import pandas as pd

from config import (
    GLOBAL_ASSETS_ENABLED,
    GLOBAL_CRYPTO_24H,
    GLOBAL_ENABLE_BUY,
    GLOBAL_ENABLE_SELL,
    GLOBAL_ENGULF_ATR_MULT,
    GLOBAL_ENTRY_INTERVAL,
    GLOBAL_FRACTAL_BARS,
    GLOBAL_HTF_INTERVAL,
    GLOBAL_LONDON_END_HOUR,
    GLOBAL_LONDON_START_HOUR,
    GLOBAL_M30_LOOKBACK_BARS,
    GLOBAL_NY_END_HOUR,
    GLOBAL_NY_START_HOUR,
    GLOBAL_RR_RATIO,
)
from data_fetcher import fetch_index_history, resample_ohlcv
from indicators import atr, ema
from market_time import is_global_market_scan_allowed, now_ist
from position_lifecycle import (
    global_bar_alerted,
    global_signal_blocked,
    mark_global_bar_alerted,
    reconcile_global_positions,
    register_global_open,
)
from telegram_client import send_plain

logger = logging.getLogger(__name__)

_STRATEGY = "Global H4 + M30 Fractal Sweep"
_ASSETS: dict[str, dict] = {
    "BTCUSD": {"ticker": "BTC-USD", "label": "Bitcoin", "crypto": True},
    "ETHUSD": {"ticker": "ETH-USD", "label": "Ethereum", "crypto": True},
    "XAUUSD": {
        "ticker": "GC=F",
        "label": "Gold",
        "tickers": ["GC=F", "XAUUSD=X", "GLD"],
        "crypto": False,
    },
}


def _round_px(px: float, symbol: str) -> float:
    if symbol == "XAUUSD":
        return round(px, 2)
    if px >= 1000:
        return round(px, 2)
    return round(px, 4)


def _normalize_df(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.capitalize)
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC")
    return raw.dropna(subset=["Close"])


def _fetch_bars(ticker: str, interval: str, period: str) -> pd.DataFrame:
    if interval == "4h":
        raw = fetch_index_history(ticker, "1h", period=period)
        raw = _normalize_df(raw)
        if raw.empty:
            return raw
        return resample_ohlcv(raw, "4h")
    raw = fetch_index_history(ticker, interval, period=period)
    return _normalize_df(raw)


def _fetch_asset_bars(meta: dict, interval: str, period: str) -> pd.DataFrame:
    tickers = meta.get("tickers") or [meta["ticker"]]
    for ticker in tickers:
        bars = _fetch_bars(ticker, interval, period)
        if bars is not None and not bars.empty:
            return bars
    return pd.DataFrame()


def _in_session_utc(ts: pd.Timestamp, symbol: str = "") -> bool:
    if symbol in ("BTCUSD", "ETHUSD") and GLOBAL_CRYPTO_24H:
        return True
    hour = int(ts.tz_convert("UTC").hour)
    london = GLOBAL_LONDON_START_HOUR <= hour < GLOBAL_LONDON_END_HOUR
    ny = GLOBAL_NY_START_HOUR <= hour < GLOBAL_NY_END_HOUR
    return london or ny


def _htf_bias(df_h4: pd.DataFrame, idx: int = -2) -> str | None:
    """H4 trend: BUY if EMA20>EMA50 and close above EMA20; SELL opposite."""
    if len(df_h4) < 55:
        return None
    close = df_h4["Close"]
    e20 = float(ema(close, 20).iloc[idx])
    e50 = float(ema(close, 50).iloc[idx])
    c = float(close.iloc[idx])
    if c > e20 > e50:
        return "BUY"
    if c < e20 < e50:
        return "SELL"
    return None


def _htf_bias_as_of(df_h4: pd.DataFrame, ts: pd.Timestamp) -> str | None:
    """H4 bias on the last completed H4 bar at or before the M30 signal time."""
    if df_h4.empty:
        return None
    ts = ts.tz_convert(df_h4.index.tz) if ts.tzinfo else ts
    hist = df_h4[df_h4.index <= ts]
    if len(hist) < 55:
        return None
    return _htf_bias(hist, -2)


def _bar_index(df: pd.DataFrame, idx: int) -> int:
    return len(df) + idx if idx < 0 else idx


def _fractal_levels(df: pd.DataFrame, side: str, idx: int, bars: int) -> list[float]:
    """Recent fractal highs/lows on completed bars before idx."""
    pi = _bar_index(df, idx)
    high = df["High"]
    low = df["Low"]
    levels: list[float] = []
    start = max(bars, pi - 40)
    end = pi - bars
    if end <= start:
        return levels
    for i in range(start, end):
        if side == "low":
            if all(float(low.iloc[i]) < float(low.iloc[i - j]) for j in range(1, bars + 1)):
                if all(float(low.iloc[i]) < float(low.iloc[i + j]) for j in range(1, bars + 1)):
                    levels.append(float(low.iloc[i]))
        else:
            if all(float(high.iloc[i]) > float(high.iloc[i - j]) for j in range(1, bars + 1)):
                if all(float(high.iloc[i]) > float(high.iloc[i + j]) for j in range(1, bars + 1)):
                    levels.append(float(high.iloc[i]))
    return levels[-5:]


def _bullish_sweep(df: pd.DataFrame, idx: int, bars: int) -> float | None:
    """Wick below fractal low, close back above — returns swept level."""
    bar = df.iloc[idx]
    lows = _fractal_levels(df, "low", idx, bars)
    if not lows:
        return None
    c = float(bar["Close"])
    lo = float(bar["Low"])
    for lvl in reversed(lows):
        if lo < lvl and c > lvl:
            return lvl
    return None


def _bearish_sweep(df: pd.DataFrame, idx: int, bars: int) -> float | None:
    bar = df.iloc[idx]
    highs = _fractal_levels(df, "high", idx, bars)
    if not highs:
        return None
    c = float(bar["Close"])
    hi = float(bar["High"])
    for lvl in reversed(highs):
        if hi > lvl and c < lvl:
            return lvl
    return None


def _bullish_engulfing(df: pd.DataFrame, idx: int, min_body: float) -> bool:
    pi = _bar_index(df, idx)
    if pi < 1:
        return False
    cur = df.iloc[pi]
    prev = df.iloc[pi - 1]
    o, c = float(cur["Open"]), float(cur["Close"])
    po, pc = float(prev["Open"]), float(prev["Close"])
    if not (pc < po and c > o):
        return False
    if not (o <= pc and c >= po):
        return False
    return abs(c - o) >= min_body


def _bearish_engulfing(df: pd.DataFrame, idx: int, min_body: float) -> bool:
    pi = _bar_index(df, idx)
    if pi < 1:
        return False
    cur = df.iloc[pi]
    prev = df.iloc[pi - 1]
    o, c = float(cur["Open"]), float(cur["Close"])
    po, pc = float(prev["Open"]), float(prev["Close"])
    if not (pc > po and c < o):
        return False
    if not (o >= pc and c <= po):
        return False
    return abs(o - c) >= min_body


def _build_trade_at_idx(
    symbol: str,
    label: str,
    df_h4: pd.DataFrame,
    df_m30: pd.DataFrame,
    idx: int,
) -> dict | None:
    if len(df_m30) < 30 or len(df_h4) < 20:
        return None
    if abs(idx) >= len(df_m30):
        return None

    ts = df_m30.index[idx]
    if not _in_session_utc(ts, symbol):
        return None

    bias = _htf_bias_as_of(df_h4, ts)
    if bias is None:
        return None

    atr_val = float(
        atr(df_m30["High"], df_m30["Low"], df_m30["Close"], 14).iloc[idx]
    )
    min_body = max(atr_val * GLOBAL_ENGULF_ATR_MULT, float(df_m30["Close"].iloc[idx]) * 0.0005)
    bars = max(1, GLOBAL_FRACTAL_BARS)

    side: str | None = None
    sweep_lvl: float | None = None

    if bias == "BUY" and GLOBAL_ENABLE_BUY:
        sweep_lvl = _bullish_sweep(df_m30, idx, bars)
        if sweep_lvl and _bullish_engulfing(df_m30, idx, min_body):
            side = "BUY"
    elif bias == "SELL" and GLOBAL_ENABLE_SELL:
        sweep_lvl = _bearish_sweep(df_m30, idx, bars)
        if sweep_lvl and _bearish_engulfing(df_m30, idx, min_body):
            side = "SELL"

    if side is None or sweep_lvl is None:
        return None

    bar = df_m30.iloc[idx]
    entry = _round_px(float(bar["Close"]), symbol)
    buffer = max(atr_val * 0.1, entry * 0.0003)

    if side == "BUY":
        stop = _round_px(float(bar["Low"]) - buffer, symbol)
        risk = entry - stop
        if risk <= 0:
            return None
        target = _round_px(entry + risk * GLOBAL_RR_RATIO, symbol)
    else:
        stop = _round_px(float(bar["High"]) + buffer, symbol)
        risk = stop - entry
        if risk <= 0:
            return None
        target = _round_px(entry - risk * GLOBAL_RR_RATIO, symbol)

    rr = GLOBAL_RR_RATIO
    if symbol in ("BTCUSD", "ETHUSD") and GLOBAL_CRYPTO_24H:
        session = "24h crypto"
    elif GLOBAL_LONDON_START_HOUR <= ts.tz_convert("UTC").hour < GLOBAL_LONDON_END_HOUR:
        session = "London"
    else:
        session = "New York"
    analysis = (
        f"H4 {'bullish' if side == 'BUY' else 'bearish'} bias (EMA20/50). "
        f"M30 fractal {'low' if side == 'BUY' else 'high'} sweep @ {sweep_lvl:.2f} + engulfing close. "
        f"Session: {session} UTC. TP fixed 1:{rr:.0f} R:R."
    )
    return {
        "symbol": symbol,
        "label": label,
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "sweep": sweep_lvl,
        "analysis": analysis,
        "signal_time": ts.isoformat(),
    }


def _build_trade(symbol: str, label: str, df_h4: pd.DataFrame, df_m30: pd.DataFrame) -> dict | None:
    lookback = max(1, GLOBAL_M30_LOOKBACK_BARS)
    for offset in range(2, 2 + lookback):
        idx = -offset
        plan = _build_trade_at_idx(symbol, label, df_h4, df_m30, idx)
        if plan:
            return plan
    return None


def _format_message(plan: dict) -> str:
    ts = now_ist().strftime("%d %b %Y, %H:%M IST")
    emoji = "🟢" if plan["side"] == "BUY" else "🔴"
    sym = html.escape(plan["symbol"])
    label = html.escape(plan["label"])
    analysis = html.escape(plan["analysis"])
    return "\n".join(
        [
            f"{emoji} <b>{sym} {plan['side']}</b> — {label}",
            f"<b>Strategy:</b> {_STRATEGY}",
            f"<b>Timeframe:</b> H4 bias · M30 entry (closed candle)",
            f"<b>Entry:</b> {plan['entry']}",
            f"<b>Stop Loss:</b> {plan['stop']} <i>(engulfing extreme)</i>",
            f"<b>Target:</b> {plan['target']} <i>(1:{plan['rr']:.0f} R:R)</i>",
            f"<b>Fractal sweep:</b> {plan['sweep']}",
            f"<b>Analysis:</b> {analysis}",
            f"<i>Outside NSE hours · London/NY session · {ts}</i>",
        ]
    )


def run_global_assets_alerts() -> int:
    """Scan BTC/ETH/XAU — H4 + M30 fractal sweep & engulfing."""
    if not GLOBAL_ASSETS_ENABLED:
        return 0
    if not is_global_market_scan_allowed():
        logger.debug("Global scan skipped — outside alert window or NSE session overlap")
        return 0

    reconcile_global_positions()
    sent = 0
    entry_iv = GLOBAL_ENTRY_INTERVAL if GLOBAL_ENTRY_INTERVAL in ("15m", "30m", "60m") else "30m"
    htf_iv = GLOBAL_HTF_INTERVAL if GLOBAL_HTF_INTERVAL in ("1h", "4h") else "4h"
    logger.info("Global assets scan starting (lookback=%s M30 bars)", GLOBAL_M30_LOOKBACK_BARS)

    for symbol, meta in _ASSETS.items():
        df_m30 = _fetch_asset_bars(meta, entry_iv, "60d")
        df_h4 = _fetch_asset_bars(meta, htf_iv, "730d")
        if df_m30 is None or df_h4 is None or df_m30.empty or df_h4.empty:
            tickers = meta.get("tickers") or [meta["ticker"]]
            logger.info("Global %s — no price data (%s)", symbol, ", ".join(tickers))
            continue

        bias = _htf_bias_as_of(df_h4, df_m30.index[-2]) if len(df_h4) >= 55 else None
        plan = _build_trade(symbol, meta["label"], df_h4, df_m30)
        if not plan:
            logger.info(
                "Global %s — no setup (H4 bias=%s, m30_bars=%d)",
                symbol,
                bias or "neutral",
                len(df_m30),
            )
            continue

        block = global_signal_blocked(
            symbol,
            plan["side"],
            plan["entry"],
            plan["stop"],
            plan["target"],
        )
        if block:
            logger.info("Skip global %s %s — %s", symbol, plan["side"], block)
            continue

        signal_time = str(plan.get("signal_time") or "")
        if global_bar_alerted(symbol, signal_time):
            logger.info("Skip global %s %s — bar already alerted (%s)", symbol, plan["side"], signal_time)
            continue

        if send_plain(_format_message(plan), html_mode=True):
            mark_global_bar_alerted(symbol, signal_time)
            register_global_open(
                symbol=symbol,
                strategy=_STRATEGY,
                side=plan["side"],
                entry=plan["entry"],
                stop_loss=plan["stop"],
                target=plan["target"],
            )
            sent += 1
            logger.info("Global M30 signal sent: %s %s @ %s", symbol, plan["side"], plan["entry"])
    if sent == 0:
        logger.info("Global assets scan complete — no new setups")
    return sent
