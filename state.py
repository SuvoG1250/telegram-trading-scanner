"""Persist watchlist and deduplicate alerts across scheduled runs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import DATA_DIR, SIGNALS_FILE, WATCHLIST_FILE
from market_time import today_key

logger = logging.getLogger(__name__)


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_watchlist() -> list[str]:
    ensure_data_dir()
    if not WATCHLIST_FILE.exists():
        return []
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        if payload.get("date") != today_key():
            return []
        return list(payload.get("symbols", []))
    except (json.JSONDecodeError, OSError):
        logger.exception("Could not read watchlist")
        return []


def save_watchlist(symbols: list[str]) -> None:
    ensure_data_dir()
    payload = {"date": today_key(), "symbols": symbols}
    WATCHLIST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_signals() -> dict[str, Any]:
    ensure_data_dir()
    if not SIGNALS_FILE.exists():
        return {"date": today_key(), "keys": []}
    try:
        return json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": today_key(), "keys": []}


def _save_signals(data: dict[str, Any]) -> None:
    ensure_data_dir()
    SIGNALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def signal_key(symbol: str, strategy: str, side: str) -> str:
    return f"{today_key()}|{symbol}|{strategy}|{side}"


def already_sent(symbol: str, strategy: str, side: str) -> bool:
    data = _load_signals()
    if data.get("date") != today_key():
        return False
    return signal_key(symbol, strategy, side) in data.get("keys", [])


def mark_sent(symbol: str, strategy: str, side: str) -> None:
    data = _load_signals()
    if data.get("date") != today_key():
        data = {"date": today_key(), "keys": []}
    key = signal_key(symbol, strategy, side)
    keys = set(data.get("keys", []))
    keys.add(key)
    data["keys"] = sorted(keys)
    _save_signals(data)
