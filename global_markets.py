"""
Global assets (BTC / ETH / XAU) — multi-strategy playbook, best signal only.

Strategies (15m unless noted):
  1. EMA 9/21 crossover + volume confirmation
  2. VWAP pullback bounce (trend continuation)
  3. Breakout / breakdown with high volume
  4. Retest entry after breakout (1:3 R:R)
  5. Opening Range Breakout — first 15 min on 5m bars

Picks the highest-scored setup per symbol per scan.
"""

from __future__ import annotations

import html
import logging

import pandas as pd

from config import (
    GLOBAL_ASSETS_ENABLED,
    GLOBAL_CRYPTO_24H,
    GLOBAL_ENTRY_INTERVAL,
    GLOBAL_LONDON_END_HOUR,
    GLOBAL_LONDON_START_HOUR,
    GLOBAL_LOOKBACK_BARS,
    GLOBAL_NY_END_HOUR,
    GLOBAL_NY_START_HOUR,
)
from data_fetcher import fetch_index_history
from global_strategy_signals import GlobalSignal, find_best_global_signal
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

_STRATEGY_LABEL = "Global Multi-Strategy (Best Signal)"
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


def _signal_to_plan(sig: GlobalSignal, symbol: str, label: str) -> dict:
    ts = pd.Timestamp(sig.signal_time)
    if symbol in ("BTCUSD", "ETHUSD") and GLOBAL_CRYPTO_24H:
        session = "24h crypto"
    elif GLOBAL_LONDON_START_HOUR <= ts.tz_convert("UTC").hour < GLOBAL_LONDON_END_HOUR:
        session = "London"
    else:
        session = "New York"

    return {
        "symbol": symbol,
        "label": label,
        "side": sig.side,
        "entry": sig.entry,
        "stop": sig.stop,
        "target": sig.target,
        "rr": sig.rr,
        "score": sig.score,
        "confidence": sig.confidence,
        "strategy": sig.strategy,
        "analysis": (
            f"{sig.analysis} · Score {sig.score:.0f}/99 ({sig.confidence}) · "
            f"Session {session} UTC · TP 1:{sig.rr:.0f} R:R"
        ),
        "signal_time": sig.signal_time,
    }


def _format_message(plan: dict) -> str:
    ts = now_ist().strftime("%d %b %Y, %H:%M IST")
    emoji = "🟢" if plan["side"] == "BUY" else "🔴"
    sym = html.escape(plan["symbol"])
    label = html.escape(plan["label"])
    strat = html.escape(plan.get("strategy") or _STRATEGY_LABEL)
    analysis = html.escape(plan["analysis"])
    conf = html.escape(plan.get("confidence", "MEDIUM"))
    score = plan.get("score", 0)
    return "\n".join(
        [
            f"{emoji} <b>{sym} {plan['side']}</b> — {label}",
            f"<b>Strategy:</b> {strat}",
            f"<b>Score:</b> {score:.0f}/99 · <b>Confidence:</b> {conf}",
            f"<b>Timeframe:</b> 15m (ORB uses 5m) · closed candle",
            f"<b>Entry:</b> {plan['entry']}",
            f"<b>Stop Loss:</b> {plan['stop']}",
            f"<b>Target:</b> {plan['target']} <i>(1:{plan['rr']:.0f} R:R)</i>",
            f"<b>Analysis:</b> {analysis}",
            f"<i>Global market only · {ts}</i>",
        ]
    )


def run_global_assets_alerts() -> int:
    """Scan BTC/ETH/XAU — run 5 strategies, alert best setup only."""
    if not GLOBAL_ASSETS_ENABLED:
        return 0
    if not is_global_market_scan_allowed():
        logger.debug("Global scan skipped — outside alert window or NSE session overlap")
        return 0

    reconcile_global_positions()
    sent = 0
    entry_iv = GLOBAL_ENTRY_INTERVAL if GLOBAL_ENTRY_INTERVAL in ("15m", "30m", "60m") else "15m"
    logger.info(
        "Global assets scan — 5 strategies, best signal only (15m+%s, lookback=%s)",
        "5m ORB",
        GLOBAL_LOOKBACK_BARS,
    )

    for symbol, meta in _ASSETS.items():
        df15 = _fetch_asset_bars(meta, entry_iv, "60d")
        df5 = _fetch_asset_bars(meta, "5m", "5d")
        if df15 is None or df15.empty:
            tickers = meta.get("tickers") or [meta["ticker"]]
            logger.info("Global %s — no price data (%s)", symbol, ", ".join(tickers))
            continue

        sig = find_best_global_signal(
            df15,
            df5 if not df5.empty else None,
            symbol,
            lookback=GLOBAL_LOOKBACK_BARS,
        )
        if not sig:
            logger.info("Global %s — no strategy setup above min score (bars=%d)", symbol, len(df15))
            continue

        ts = pd.Timestamp(sig.signal_time)
        if not _in_session_utc(ts, symbol):
            logger.info("Global %s — signal outside session window", symbol)
            continue

        plan = _signal_to_plan(sig, symbol, meta["label"])

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
                strategy=plan.get("strategy") or _STRATEGY_LABEL,
                side=plan["side"],
                entry=plan["entry"],
                stop_loss=plan["stop"],
                target=plan["target"],
            )
            sent += 1
            logger.info(
                "Global signal sent: %s %s @ %s [%s score=%.0f]",
                symbol,
                plan["side"],
                plan["entry"],
                plan.get("strategy"),
                plan.get("score", 0),
            )
    if sent == 0:
        logger.info("Global assets scan complete — no new setups")
    return sent
