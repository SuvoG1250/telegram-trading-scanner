"""Daily Upstox execution index filter — Nifty vs Sensex options chain."""

from __future__ import annotations

import logging
from typing import Literal

from upstox_trade_state import load_trade_state, save_trade_state

logger = logging.getLogger(__name__)

ExecutionIndex = Literal["nifty", "sensex"]

INDEX_LABELS = {
    "nifty": "Nifty 50",
    "sensex": "Sensex",
}


def clear_execution_index() -> None:
    data = load_trade_state()
    data.pop("execution_index", None)
    save_trade_state(data)


def get_execution_index() -> ExecutionIndex | None:
    raw = str(load_trade_state().get("execution_index") or "").strip().lower()
    if raw in ("nifty", "sensex"):
        return raw  # type: ignore[return-value]
    return None


def set_execution_index(key: ExecutionIndex) -> None:
    data = load_trade_state()
    data["execution_index"] = key
    save_trade_state(data)
    logger.info("Upstox execution index -> %s", key)


def execution_index_label() -> str:
    key = get_execution_index()
    if not key:
        return "Not selected"
    return INDEX_LABELS.get(key, key)


def signal_instrument_bucket(instrument: str) -> ExecutionIndex | None:
    inst = (instrument or "").strip().upper()
    if inst == "NIFTY_OPTION":
        return "nifty"
    if inst == "SENSEX_OPTION":
        return "sensex"
    return None


def signal_allowed_for_index(instrument: str) -> bool:
    selected = get_execution_index()
    if not selected:
        return False
    bucket = signal_instrument_bucket(instrument)
    return bucket == selected


def execution_index_status_line() -> str:
    key = get_execution_index()
    if not key:
        return "<b>Trade index:</b> ⏸ Not selected — tap <code>/menu</code> or morning buttons"
    return f"<b>Trade index:</b> {INDEX_LABELS[key]}"
