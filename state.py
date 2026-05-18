"""Persist watchlist and deduplicate alerts across scheduled runs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from datetime import datetime

import pytz

from config import DATA_DIR, SESSION_FILE, SIGNALS_FILE, WATCHLIST_FILE
from market_time import IST, today_key

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


def save_watchlist(symbols: list[str], *, locked: bool = False) -> None:
    ensure_data_dir()
    payload = {"date": today_key(), "symbols": symbols, "locked": locked}
    WATCHLIST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_watchlist_locked() -> bool:
    ensure_data_dir()
    if not WATCHLIST_FILE.exists():
        return False
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        if payload.get("date") != today_key():
            return False
        return bool(payload.get("locked", False))
    except (json.JSONDecodeError, OSError):
        return False


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


def symbol_side_key(symbol: str, side: str) -> str:
    """One combined alert per stock per side per day."""
    return f"{today_key()}|{symbol}|combined|{side}"


def already_sent(symbol: str, strategy: str, side: str) -> bool:
    data = _load_signals()
    if data.get("date") != today_key():
        return False
    return signal_key(symbol, strategy, side) in data.get("keys", [])


def already_sent_combined(symbol: str, side: str) -> bool:
    data = _load_signals()
    if data.get("date") != today_key():
        return False
    return symbol_side_key(symbol, side) in data.get("keys", [])


def mark_sent(symbol: str, strategy: str, side: str) -> None:
    data = _load_signals()
    if data.get("date") != today_key():
        data = {"date": today_key(), "keys": []}
    keys = set(data.get("keys", []))
    keys.add(signal_key(symbol, strategy, side))
    data["keys"] = sorted(keys)
    _save_signals(data)


def mark_sent_combined(symbol: str, side: str, strategies: list[str]) -> None:
    data = _load_signals()
    if data.get("date") != today_key():
        data = {"date": today_key(), "keys": []}
    keys = set(data.get("keys", []))
    keys.add(symbol_side_key(symbol, side))
    for strat in strategies:
        keys.add(signal_key(symbol, strat, side))
    data["keys"] = sorted(keys)
    _save_signals(data)


def _load_session() -> dict[str, Any]:
    ensure_data_dir()
    if not SESSION_FILE.exists():
        return {"date": today_key(), "start_sent": False, "stop_sent": False}
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": today_key(), "start_sent": False, "stop_sent": False}


def _save_session(data: dict[str, Any]) -> None:
    ensure_data_dir()
    SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _session_today() -> dict[str, Any]:
    data = _load_session()
    if data.get("date") != today_key():
        data = {"date": today_key(), "start_sent": False, "stop_sent": False}
    return data


def session_start_sent() -> bool:
    return bool(_session_today().get("start_sent"))


def session_stop_sent() -> bool:
    return bool(_session_today().get("stop_sent"))


def mark_session_start() -> None:
    data = _session_today()
    data["start_sent"] = True
    _save_session(data)


def mark_session_stop() -> None:
    data = _session_today()
    data["stop_sent"] = True
    _save_session(data)


def daily_summary_sent() -> bool:
    return bool(_session_today().get("daily_summary_sent"))


def mark_daily_summary() -> None:
    data = _session_today()
    data["daily_summary_sent"] = True
    _save_session(data)


def automation_boot_sent() -> bool:
    return bool(_session_today().get("automation_boot_sent"))


def mark_automation_boot() -> None:
    data = _session_today()
    data["automation_boot_sent"] = True
    _save_session(data)


def record_trading_started_at() -> None:
    """First intraday / session start of the day (used for 30-min delayed boot)."""
    data = _session_today()
    if not data.get("trading_started_at"):
        data["trading_started_at"] = datetime.now(IST).isoformat()
        _save_session(data)


def get_trading_started_at() -> datetime | None:
    raw = _session_today().get("trading_started_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = IST.localize(dt)
        return dt
    except (TypeError, ValueError):
        return None


def long_term_picks_sent() -> bool:
    return bool(_session_today().get("long_term_picks_sent"))


def mark_long_term_picks_sent() -> None:
    data = _session_today()
    data["long_term_picks_sent"] = True
    _save_session(data)


def _consolidation_key(symbol: str) -> str:
    return f"consolidation_active|{symbol}"


def mark_consolidation_active(symbol: str) -> None:
    data = _load_signals()
    if data.get("date") != today_key():
        data = {"date": today_key(), "keys": []}
    keys = set(data.get("keys", []))
    keys.add(_consolidation_key(symbol))
    data["keys"] = sorted(keys)
    _save_signals(data)


def is_consolidation_active(symbol: str) -> bool:
    data = _load_signals()
    if data.get("date") != today_key():
        return False
    return _consolidation_key(symbol) in data.get("keys", [])


def clear_consolidation_active(symbol: str) -> None:
    data = _load_signals()
    if data.get("date") != today_key():
        return
    keys = set(data.get("keys", []))
    keys.discard(_consolidation_key(symbol))
    data["keys"] = sorted(keys)
    _save_signals(data)
