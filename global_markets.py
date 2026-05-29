"""Global assets scanner (BTCUSD, ETHUSD, XAUUSD) with 1:3 to 1:6 plans."""

from __future__ import annotations

import html
import logging

import yfinance as yf

from config import (
    GLOBAL_ASSETS_ENABLED,
    GLOBAL_ATR_SL_MULT,
    GLOBAL_RR_MAX,
    GLOBAL_RR_MIN,
)
from indicators import atr, ema, rsi
from market_time import is_global_alert_window, now_ist
from position_lifecycle import (
    global_position_open,
    reconcile_global_positions,
    register_global_open,
)
from telegram_client import send_plain

logger = logging.getLogger(__name__)

_STRATEGY = "Global EMA-RSI ATR"
_ASSETS: dict[str, dict[str, str]] = {
    "BTCUSD": {"ticker": "BTC-USD", "label": "Bitcoin"},
    "ETHUSD": {"ticker": "ETH-USD", "label": "Ethereum"},
    "XAUUSD": {"ticker": "XAUUSD=X", "label": "Gold Spot"},
}


def _fetch_asset_df(ticker: str):
    try:
        df = yf.Ticker(ticker).history(period="10d", interval="15m", auto_adjust=True)
    except Exception:
        logger.exception("Global fetch failed for %s", ticker)
        return None
    if df is None or df.empty:
        return None
    if len(df) < 80:
        return None
    return df.dropna()


def _rr_from_strength(close_px: float, ema_fast: float, ema_slow: float, rsi_val: float) -> float:
    spread_pct = abs(ema_fast - ema_slow) / max(close_px, 1e-9) * 100.0
    rsi_bias = abs(rsi_val - 50.0) / 25.0
    strength = spread_pct + rsi_bias
    if strength >= 2.0:
        return min(6.0, max(3.0, GLOBAL_RR_MAX))
    if strength >= 1.0:
        return min(6.0, max(3.0, (GLOBAL_RR_MIN + GLOBAL_RR_MAX) / 2.0))
    return min(6.0, max(3.0, GLOBAL_RR_MIN))


def _build_trade(symbol: str, label: str, df):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    rsi14 = rsi(close, 14)
    atr14 = atr(high, low, close, 14)

    c = float(close.iloc[-1])
    f = float(ema20.iloc[-1])
    s = float(ema50.iloc[-1])
    r = float(rsi14.iloc[-1])
    a = float(atr14.iloc[-1])
    if c <= 0 or a <= 0:
        return None

    if c > f > s and 50 <= r <= 75:
        side = "BUY"
    elif c < f < s and 25 <= r <= 50:
        side = "SELL"
    else:
        return None

    sl_dist = max(a * max(0.8, GLOBAL_ATR_SL_MULT), c * 0.002)
    rr = _rr_from_strength(c, f, s, r)
    if side == "BUY":
        stop = c - sl_dist
        target = c + (c - stop) * rr
    else:
        stop = c + sl_dist
        target = c - (stop - c) * rr

    trend = "bullish trend continuation" if side == "BUY" else "bearish trend continuation"
    analysis = (
        f"Price vs EMA20/EMA50 confirms {trend}; RSI={r:.1f} and ATR(14)={a:.2f} "
        f"show {'healthy momentum' if 40 <= r <= 70 else 'volatile momentum'}."
    )
    return {
        "symbol": symbol,
        "label": label,
        "side": side,
        "entry": c,
        "stop": stop,
        "target": target,
        "rr": rr,
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
            f"<b>Strategy:</b> {_STRATEGY} (15m MTF trend)",
            f"<b>Entry:</b> {plan['entry']:.2f}",
            f"<b>Stop Loss:</b> {plan['stop']:.2f}",
            f"<b>Target:</b> {plan['target']:.2f}",
            f"<b>Risk:Reward:</b> 1:{plan['rr']:.2f}",
            f"<b>Market Analysis:</b> {analysis}",
            f"<i>Global window: 07:00–23:00 IST · {ts}</i>",
        ]
    )


def run_global_assets_alerts() -> int:
    """Scan BTCUSD/ETHUSD/XAUUSD; one open plan per symbol until SL or target."""
    if not GLOBAL_ASSETS_ENABLED or not is_global_alert_window():
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
        if global_position_open(symbol):
            logger.debug("Skip %s — active global plan awaiting SL/Target.", symbol)
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
            logger.info("Global signal sent: %s %s", symbol, plan["side"])
    return sent
