"""Global assets scanner (BTCUSD, ETHUSD, XAUUSD) — 1H structure-based plans."""

from __future__ import annotations

import html
import logging

import yfinance as yf

from config import GLOBAL_ASSETS_ENABLED, GLOBAL_INTERVAL, GLOBAL_MIN_RR, GLOBAL_MAX_RR
from indicators import atr, compute_supertrend, ema, rsi
from market_time import is_global_market_scan_allowed, now_ist
from position_lifecycle import (
    global_signal_blocked,
    reconcile_global_positions,
    register_global_open,
)
from telegram_client import send_plain

logger = logging.getLogger(__name__)

_STRATEGY = "Global 1H Structure + Trend"
_INTERVAL = GLOBAL_INTERVAL if GLOBAL_INTERVAL in ("1h", "60m") else "1h"
_ASSETS: dict[str, dict[str, str]] = {
    "BTCUSD": {"ticker": "BTC-USD", "label": "Bitcoin"},
    "ETHUSD": {"ticker": "ETH-USD", "label": "Ethereum"},
    "XAUUSD": {"ticker": "XAUUSD=X", "label": "Gold Spot"},
}


def _round_px(px: float, symbol: str) -> float:
    if symbol == "XAUUSD":
        return round(px, 2)
    if px >= 1000:
        return round(px, 2)
    return round(px, 4)


def _fetch_asset_df(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="60d", interval=_INTERVAL, auto_adjust=True)
    except Exception:
        logger.exception("Global fetch failed for %s", ticker)
        return None
    if df is None or df.empty or len(df) < 55:
        return None
    df = df.dropna()
    if len(df) < 55:
        return None
    return df


def _confirmed_trend(df, idx: int = -2) -> str | None:
    """Return BUY/SELL only on last *completed* 1H candle with multi-factor confirm."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    rsi14 = rsi(close, 14)
    st = compute_supertrend(df, length=10, multiplier=3.0)

    c = float(close.iloc[idx])
    f = float(ema20.iloc[idx])
    s = float(ema50.iloc[idx])
    r = float(rsi14.iloc[idx])
    st_dir = float(st["direction"].iloc[idx])  # -1 bullish, +1 bearish (Pine)

    prev_c = float(close.iloc[idx - 1])
    prev_f = float(ema20.iloc[idx - 1])

    # Higher-low / lower-high on last 3 completed bars
    lows = [float(low.iloc[i]) for i in range(idx - 2, idx + 1)]
    highs = [float(high.iloc[i]) for i in range(idx - 2, idx + 1)]
    higher_lows = lows[1] > lows[0] and lows[2] > lows[1]
    lower_highs = highs[1] < highs[0] and highs[2] < highs[1]

    if c > f > s and r >= 52 and r <= 72 and st_dir < 0 and c > prev_c and c > prev_f:
        if higher_lows or c > float(high.iloc[idx - 1]):
            return "BUY"
    if c < f < s and r >= 28 and r <= 48 and st_dir > 0 and c < prev_c and c < prev_f:
        if lower_highs or c < float(low.iloc[idx - 1]):
            return "SELL"
    return None


def _structure_levels(df, side: str, entry: float, symbol: str, idx: int = -2):
    """SL/target from swing structure on 1H — not fixed ATR multiples."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    atr14 = float(atr(high, low, close, 14).iloc[idx])
    lookback = df.iloc[idx - 20 : idx]  # completed bars only
    if len(lookback) < 10:
        return None

    buffer = max(atr14 * 0.15, entry * 0.0008)

    if side == "BUY":
        swing_low = float(lookback["Low"].min())
        stop = swing_low - buffer
        if stop >= entry:
            stop = entry - max(atr14 * 0.8, entry * 0.003)

        resistance = float(lookback["High"].max())
        if resistance <= entry:
            resistance = entry + atr14 * 2.0
        # Next structural target: recent swing high above entry
        highs_above = [float(h) for h in lookback["High"] if float(h) > entry * 1.001]
        target = max(highs_above) if highs_above else resistance
        if target <= entry:
            target = entry + (entry - stop) * GLOBAL_MIN_RR
    else:
        swing_high = float(lookback["High"].max())
        stop = swing_high + buffer
        if stop <= entry:
            stop = entry + max(atr14 * 0.8, entry * 0.003)

        support = float(lookback["Low"].min())
        if support >= entry:
            support = entry - atr14 * 2.0
        lows_below = [float(l) for l in lookback["Low"] if float(l) < entry * 0.999]
        target = min(lows_below) if lows_below else support
        if target >= entry:
            target = entry - (stop - entry) * GLOBAL_MIN_RR

    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk
    if rr < GLOBAL_MIN_RR:
        # Extend target to minimum structural RR only if still realistic
        if side == "BUY":
            target = entry + risk * GLOBAL_MIN_RR
        else:
            target = entry - risk * GLOBAL_MIN_RR
        reward = abs(target - entry)
        rr = reward / risk

    if rr < GLOBAL_MIN_RR or rr > GLOBAL_MAX_RR:
        return None

    return {
        "stop": _round_px(stop, symbol),
        "target": _round_px(target, symbol),
        "rr": round(rr, 2),
        "atr": atr14,
    }


def _build_trade(symbol: str, label: str, df):
    side = _confirmed_trend(df, idx=-2)
    if side is None:
        return None

    entry = _round_px(float(df["Close"].iloc[-2]), symbol)
    levels = _structure_levels(df, side, entry, symbol, idx=-2)
    if levels is None:
        return None

    close = df["Close"]
    rsi_val = float(rsi(close, 14).iloc[-2])
    ema20 = float(ema(close, 20).iloc[-2])
    ema50 = float(ema(close, 50).iloc[-2])

    if side == "BUY":
        trend = "1H bullish structure (EMA stack + Supertrend + higher lows)"
    else:
        trend = "1H bearish structure (EMA stack + Supertrend + lower highs)"

    analysis = (
        f"{trend}. Entry on last closed 1H candle. "
        f"SL at swing {'low' if side == 'BUY' else 'high'} + buffer; "
        f"target at nearest {'resistance' if side == 'BUY' else 'support'}. "
        f"RSI={rsi_val:.1f}, EMA20={ema20:.2f}, EMA50={ema50:.2f}, ATR={levels['atr']:.2f}."
    )
    return {
        "symbol": symbol,
        "label": label,
        "side": side,
        "entry": entry,
        "stop": levels["stop"],
        "target": levels["target"],
        "rr": levels["rr"],
        "analysis": analysis,
    }


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
            f"<b>Timeframe:</b> 1 Hour (confirmed closed candle)",
            f"<b>Entry:</b> {plan['entry']:.2f}",
            f"<b>Stop Loss:</b> {plan['stop']:.2f} <i>(structure)</i>",
            f"<b>Target:</b> {plan['target']:.2f} <i>(structure)</i>",
            f"<b>Risk:Reward:</b> 1:{plan['rr']:.2f}",
            f"<b>Market Analysis:</b> {analysis}",
            f"<i>Outside NSE hours · 07:00–23:00 IST · {ts}</i>",
        ]
    )


def run_global_assets_alerts() -> int:
    """Scan BTCUSD/ETHUSD/XAUUSD on 1H; skip NSE hours and duplicate ranges."""
    if not GLOBAL_ASSETS_ENABLED or not is_global_market_scan_allowed():
        return 0

    reconcile_global_positions()
    sent = 0
    for symbol, meta in _ASSETS.items():
        df = _fetch_asset_df(meta["ticker"])
        if df is None:
            continue
        plan = _build_trade(symbol, meta["label"], df)
        if not plan:
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

        if send_plain(_format_message(plan), html_mode=True):
            register_global_open(
                symbol=symbol,
                strategy=_STRATEGY,
                side=plan["side"],
                entry=plan["entry"],
                stop_loss=plan["stop"],
                target=plan["target"],
            )
            sent += 1
            logger.info("Global 1H signal sent: %s %s", symbol, plan["side"])
    return sent
