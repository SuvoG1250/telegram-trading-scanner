"""
Global assets (BTC / ETH / XAU) — EMA 9/21 crossover + MACD histogram sync on 15m.

Strategy:
  • 15-minute OHLC candles
  • BUY: EMA 9 crosses above EMA 21 AND MACD histogram turns green on same bar
  • SELL: EMA 9 crosses below EMA 21 AND MACD histogram turns red on same bar
  • SL — beyond signal bar extreme / EMA 21 (whichever is wider)
  • TP — fixed R:R (configurable, default 1:2)
  • Crypto (BTC/ETH): 24h · Gold: London or New York session (UTC)
  • One active plan per symbol until SL/target
"""

from __future__ import annotations

import html
import logging

import pandas as pd

from config import (
    GLOBAL_ASSETS_ENABLED,
    GLOBAL_CRYPTO_24H,
    GLOBAL_EMA_FAST,
    GLOBAL_EMA_SLOW,
    GLOBAL_ENABLE_BUY,
    GLOBAL_ENABLE_SELL,
    GLOBAL_ENTRY_INTERVAL,
    GLOBAL_LONDON_END_HOUR,
    GLOBAL_LONDON_START_HOUR,
    GLOBAL_LOOKBACK_BARS,
    GLOBAL_MACD_FAST,
    GLOBAL_MACD_SIGNAL,
    GLOBAL_MACD_SLOW,
    GLOBAL_NY_END_HOUR,
    GLOBAL_NY_START_HOUR,
    GLOBAL_RR_RATIO,
)
from data_fetcher import fetch_index_history
from indicators import atr, compute_macd, ema
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

_STRATEGY = "Global EMA 9/21 + MACD (15m)"
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


def _fetch_asset_bars(meta: dict, interval: str, period: str) -> pd.DataFrame:
    tickers = meta.get("tickers") or [meta["ticker"]]
    for ticker in tickers:
        raw = fetch_index_history(ticker, interval, period=period)
        bars = _normalize_df(raw)
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


def _ema_macd_side(df: pd.DataFrame, idx: int) -> str | None:
    """
    EMA 9/21 cross on bar idx vs idx-1 AND MACD histogram color flip on same bar.
    Returns BUY or SELL.
    """
    pi = len(df) + idx if idx < 0 else idx
    if pi < 3 or pi >= len(df):
        return None

    close = df["Close"]
    e_fast = ema(close, GLOBAL_EMA_FAST)
    e_slow = ema(close, GLOBAL_EMA_SLOW)
    macd = compute_macd(
        close,
        fast=GLOBAL_MACD_FAST,
        slow=GLOBAL_MACD_SLOW,
        signal=GLOBAL_MACD_SIGNAL,
    )
    hist = macd["histogram"]

    prev = df.iloc[pi - 1]
    cur = df.iloc[pi]
    prev_hist = float(hist.iloc[pi - 1])
    cur_hist = float(hist.iloc[pi])

    bull_cross = (
        float(e_fast.iloc[pi - 1]) <= float(e_slow.iloc[pi - 1])
        and float(e_fast.iloc[pi]) > float(e_slow.iloc[pi])
    )
    bear_cross = (
        float(e_fast.iloc[pi - 1]) >= float(e_slow.iloc[pi - 1])
        and float(e_fast.iloc[pi]) < float(e_slow.iloc[pi])
    )
    turned_green = cur_hist > 0 and prev_hist <= 0
    turned_red = cur_hist < 0 and prev_hist >= 0

    if bull_cross and turned_green and GLOBAL_ENABLE_BUY:
        return "BUY"
    if bear_cross and turned_red and GLOBAL_ENABLE_SELL:
        return "SELL"
    return None


def _build_trade_at_idx(
    symbol: str,
    label: str,
    df: pd.DataFrame,
    idx: int,
) -> dict | None:
    min_len = max(GLOBAL_MACD_SLOW, GLOBAL_EMA_SLOW, GLOBAL_MACD_SIGNAL) + 5
    if len(df) < min_len:
        return None
    if abs(idx) >= len(df):
        return None

    ts = df.index[idx]
    if not _in_session_utc(ts, symbol):
        return None

    side = _ema_macd_side(df, idx)
    if side is None:
        return None

    bar = df.iloc[idx]
    entry = _round_px(float(bar["Close"]), symbol)
    ema21 = float(ema(df["Close"], GLOBAL_EMA_SLOW).iloc[idx])
    atr_val = float(atr(df["High"], df["Low"], df["Close"], 14).iloc[idx])
    buffer = max(atr_val * 0.1, entry * 0.0003)

    hist = float(
        compute_macd(
            df["Close"],
            fast=GLOBAL_MACD_FAST,
            slow=GLOBAL_MACD_SLOW,
            signal=GLOBAL_MACD_SIGNAL,
        )["histogram"].iloc[idx]
    )
    prev_hist = float(
        compute_macd(
            df["Close"],
            fast=GLOBAL_MACD_FAST,
            slow=GLOBAL_MACD_SLOW,
            signal=GLOBAL_MACD_SIGNAL,
        )["histogram"].iloc[idx - 1]
    )

    if side == "BUY":
        stop = _round_px(min(float(bar["Low"]), ema21) - buffer, symbol)
        risk = entry - stop
        if risk <= 0:
            return None
        target = _round_px(entry + risk * GLOBAL_RR_RATIO, symbol)
        cross = "EMA 9 crossed above EMA 21"
        macd_tag = f"MACD histogram green ({prev_hist:.4f}→{hist:.4f})"
    else:
        stop = _round_px(max(float(bar["High"]), ema21) + buffer, symbol)
        risk = stop - entry
        if risk <= 0:
            return None
        target = _round_px(entry - risk * GLOBAL_RR_RATIO, symbol)
        cross = "EMA 9 crossed below EMA 21"
        macd_tag = f"MACD histogram red ({prev_hist:.4f}→{hist:.4f})"

    rr = GLOBAL_RR_RATIO
    if symbol in ("BTCUSD", "ETHUSD") and GLOBAL_CRYPTO_24H:
        session = "24h crypto"
    elif GLOBAL_LONDON_START_HOUR <= ts.tz_convert("UTC").hour < GLOBAL_LONDON_END_HOUR:
        session = "London"
    else:
        session = "New York"

    analysis = (
        f"{cross} · {macd_tag} "
        f"(MACD {GLOBAL_MACD_FAST}/{GLOBAL_MACD_SLOW}/{GLOBAL_MACD_SIGNAL}) · "
        f"15m close · EMA21 ref {ema21:.2f} · Session: {session} UTC · TP 1:{rr:.0f} R:R"
    )
    return {
        "symbol": symbol,
        "label": label,
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "ema21": ema21,
        "hist": hist,
        "analysis": analysis,
        "signal_time": ts.isoformat(),
    }


def _build_trade(symbol: str, label: str, df: pd.DataFrame) -> dict | None:
    lookback = max(1, GLOBAL_LOOKBACK_BARS)
    for offset in range(2, 2 + lookback):
        plan = _build_trade_at_idx(symbol, label, df, -offset)
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
            f"<b>Timeframe:</b> 15m · closed candle",
            f"<b>Entry:</b> {plan['entry']}",
            f"<b>Stop Loss:</b> {plan['stop']}",
            f"<b>Target:</b> {plan['target']} <i>(1:{plan['rr']:.0f} R:R)</i>",
            f"<b>EMA 21:</b> {plan['ema21']:.4f} · <b>MACD hist:</b> {plan['hist']:.4f}",
            f"<b>Analysis:</b> {analysis}",
            f"<i>Outside NSE hours · {ts}</i>",
        ]
    )


def run_global_assets_alerts() -> int:
    """Scan BTC/ETH/XAU — EMA 9/21 + MACD sync on 15m."""
    if not GLOBAL_ASSETS_ENABLED:
        return 0
    if not is_global_market_scan_allowed():
        logger.debug("Global scan skipped — outside alert window or NSE session overlap")
        return 0

    reconcile_global_positions()
    sent = 0
    entry_iv = GLOBAL_ENTRY_INTERVAL if GLOBAL_ENTRY_INTERVAL in ("15m", "30m", "60m") else "15m"
    logger.info("Global assets scan starting (15m EMA+MACD, lookback=%s bars)", GLOBAL_LOOKBACK_BARS)

    for symbol, meta in _ASSETS.items():
        df = _fetch_asset_bars(meta, entry_iv, "60d")
        if df is None or df.empty:
            tickers = meta.get("tickers") or [meta["ticker"]]
            logger.info("Global %s — no price data (%s)", symbol, ", ".join(tickers))
            continue

        plan = _build_trade(symbol, meta["label"], df)
        if not plan:
            logger.info("Global %s — no EMA+MACD setup (bars=%d)", symbol, len(df))
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
            logger.info("Global 15m EMA+MACD signal sent: %s %s @ %s", symbol, plan["side"], plan["entry"])
    if sent == 0:
        logger.info("Global assets scan complete — no new setups")
    return sent
