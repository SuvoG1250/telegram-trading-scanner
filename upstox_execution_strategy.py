"""Daily Upstox auto-execution strategy selection (ST+TSL vs EMA+MACD Sync)."""

from __future__ import annotations

import logging
from typing import Literal

from upstox_trade_state import load_trade_state, save_trade_state

logger = logging.getLogger(__name__)

ExecutionStrategy = Literal["st_tsl", "ema_macd_sync"]

ST_TSL_STRATEGIES = frozenset(
    {
        "Nifty ST+TSL Options",
        "Sensex ST+TSL Options",
    }
)
EMA_MACD_SYNC_STRATEGIES = frozenset(
    {
        "EMA 9/21 + MACD Sync Options",
    }
)

STRATEGY_LABELS = {
    "st_tsl": "Original Strategy (ST+TSL · 5m)",
    "ema_macd_sync": "9/21 EMA + MACD Strategy (3m HA)",
}


def clear_execution_strategy() -> None:
    data = load_trade_state()
    data.pop("execution_strategy", None)
    save_trade_state(data)
    logger.info("Upstox execution strategy cleared (paused).")


def get_execution_strategy() -> ExecutionStrategy | None:
    raw = str(load_trade_state().get("execution_strategy") or "").strip().lower()
    if raw in ("st_tsl", "ema_macd_sync"):
        return raw  # type: ignore[return-value]
    return None


def set_execution_strategy(key: ExecutionStrategy) -> None:
    data = load_trade_state()
    data["execution_strategy"] = key
    save_trade_state(data)
    logger.info("Upstox execution strategy -> %s", key)


def execution_strategy_label() -> str:
    key = get_execution_strategy()
    if not key:
        return "Not selected"
    return STRATEGY_LABELS.get(key, key)


def signal_strategy_bucket(strategy_name: str) -> ExecutionStrategy | None:
    name = (strategy_name or "").strip()
    if name in ST_TSL_STRATEGIES:
        return "st_tsl"
    if name in EMA_MACD_SYNC_STRATEGIES:
        return "ema_macd_sync"
    return None


def signal_allowed_for_execution(signal_strategy: str) -> bool:
    """Alerts always fire; auto-orders only for today's selected bucket."""
    selected = get_execution_strategy()
    if not selected:
        return False
    bucket = signal_strategy_bucket(signal_strategy)
    return bucket == selected


def execution_strategy_status_line() -> str:
    key = get_execution_strategy()
    if not key:
        return "<b>Auto-exec strategy:</b> ⏸ PAUSED — tap morning buttons or /strategy"
    return f"<b>Auto-exec strategy:</b> {STRATEGY_LABELS[key]}"
