"""Runtime Upstox trade mode — controlled via Telegram commands or env."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from config import DATA_DIR, UPSTOX_AUTO_TRADE_ENABLED, UPSTOX_DEFAULT_LOTS
from market_time import today_key

logger = logging.getLogger(__name__)

TradeMode = Literal["off", "paper", "live"]

_STATE_FILE = DATA_DIR / "upstox_trade_state.json"


def _fresh_state() -> dict:
    return {"date": today_key(), "mode": "off", "lots": UPSTOX_DEFAULT_LOTS}


def _load() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _STATE_FILE.exists():
        return _fresh_state()
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _fresh_state()
    if data.get("date") != today_key():
        return _fresh_state()
    data.setdefault("mode", "off")
    data.setdefault("lots", UPSTOX_DEFAULT_LOTS)
    return data


def _save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data["date"] = today_key()
    _STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_mode() -> TradeMode:
    mode = str(_load().get("mode", "off")).lower()
    if mode not in ("off", "paper", "live"):
        return "off"
    return mode  # type: ignore[return-value]


def set_mode(mode: TradeMode) -> None:
    data = _load()
    data["mode"] = mode
    _save(data)
    logger.info("Upstox trade mode -> %s", mode)


def get_lots() -> int:
    return max(1, int(_load().get("lots") or UPSTOX_DEFAULT_LOTS))


def set_lots(n: int) -> None:
    data = _load()
    data["lots"] = max(1, min(10, int(n)))
    _save(data)


def auto_trade_enabled() -> bool:
    if not UPSTOX_AUTO_TRADE_ENABLED:
        return False
    return get_mode() in ("paper", "live")


def paper_trade() -> bool:
    return get_mode() == "paper"


def is_live_trading() -> bool:
    return get_mode() == "live"


def status_text() -> str:
    mode = get_mode()
    labels = {
        "off": "⏹ OFF — no Upstox orders",
        "paper": "📝 PAPER — test orders only",
        "live": "🔴 LIVE — real money option orders",
    }
    return (
        f"<b>Upstox trading:</b> {labels.get(mode, mode)}\n"
        f"<b>Lots:</b> {get_lots()} (Nifty lot=75, Sensex lot=20)\n"
        f"<b>Scope:</b> Nifty + Sensex <b>options only</b>"
    )
