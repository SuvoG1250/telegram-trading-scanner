"""Daily Upstox auto-execution strategy selection (ST+TSL vs EMA+MACD Sync)."""

from __future__ import annotations

import json
import logging
from typing import Literal

from config import DATA_DIR
from market_time import today_key

logger = logging.getLogger(__name__)

ExecutionStrategy = Literal["st_tsl", "ema_macd_sync"]

_STATE_FILE = DATA_DIR / "upstox_trade_state.json"

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
    "st_tsl": "ST+TSL (SuperTrend flip · 5m)",
    "ema_macd_sync": "EMA 9/21 + MACD Sync (cross + hist flip · 3m HA)",
}


def _load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _STATE_FILE.exists():
        return {"date": today_key()}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": today_key()}
    if data.get("date") != today_key():
        data = {"date": today_key()}
    return data


def _save_patch(patch: dict) -> None:
    data = _load()
    data.update(patch)
    data["date"] = today_key()
    _STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_execution_strategy() -> ExecutionStrategy | None:
    raw = str(_load().get("execution_strategy") or "").strip().lower()
    if raw in ("st_tsl", "ema_macd_sync"):
        return raw  # type: ignore[return-value]
    return None


def set_execution_strategy(key: ExecutionStrategy) -> None:
    _save_patch({"execution_strategy": key})
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
        return "<b>Auto-exec strategy:</b> ⚠️ not selected — use <b>/live</b> to choose"
    return f"<b>Auto-exec strategy:</b> {STRATEGY_LABELS[key]}"
