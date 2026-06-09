"""Upstox WebSocket market feed — live LTP cache (SDK MarketDataStreamerV3)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from config import UPSTOX_ACCESS_TOKEN, UPSTOX_WS_ENABLED, UPSTOX_WS_MODE
from upstox_api import upstox_configured

logger = logging.getLogger(__name__)

_ltp_cache: dict[str, float] = {}
_subscribed: set[str] = set()
_streamer: Any = None
_thread: threading.Thread | None = None
_lock = threading.Lock()
_running = False


def get_ws_ltp(instrument_key: str) -> float | None:
    with _lock:
        val = _ltp_cache.get(instrument_key)
    return val if val and val > 0 else None


def subscribe_instruments(keys: list[str]) -> None:
    keys = [k for k in keys if k]
    if not keys:
        return
    with _lock:
        new = [k for k in keys if k not in _subscribed]
        _subscribed.update(keys)
    if not new or _streamer is None:
        return
    try:
        _streamer.subscribe(new, UPSTOX_WS_MODE)
        logger.info("Upstox WS subscribed %s instrument(s).", len(new))
    except Exception:
        logger.exception("Upstox WS subscribe failed")


def _parse_ltpc(message: Any) -> None:
    if not isinstance(message, dict):
        return
    feeds = message.get("feeds") or {}
    if not isinstance(feeds, dict):
        return
    with _lock:
        for key, payload in feeds.items():
            if not isinstance(payload, dict):
                continue
            ltpc = payload.get("ltpc") or payload.get("fullFeed", {}).get("ltpc")
            if isinstance(ltpc, dict):
                ltp = ltpc.get("ltp")
            else:
                ltp = payload.get("ltp") or payload.get("last_price")
            if ltp is not None:
                try:
                    _ltp_cache[key] = float(ltp)
                except (TypeError, ValueError):
                    pass


def _run_streamer() -> None:
    global _streamer, _running
    try:
        import upstox_client
        from upstox_client import ApiClient, Configuration

        configuration = Configuration()
        configuration.access_token = UPSTOX_ACCESS_TOKEN
        api_client = ApiClient(configuration)

        with _lock:
            initial = list(_subscribed)

        _streamer = upstox_client.MarketDataStreamerV3(api_client, initial, UPSTOX_WS_MODE)
        _streamer.auto_reconnect(True, 5, 50)

        def on_message(msg: Any) -> None:
            _parse_ltpc(msg)

        _streamer.on("message", on_message)
        _streamer.on("error", lambda e: logger.warning("Upstox WS error: %s", e))
        _running = True
        logger.info("Upstox WebSocket connecting (mode=%s)...", UPSTOX_WS_MODE)
        _streamer.connect()
    except Exception:
        logger.exception("Upstox WebSocket thread exited")
    finally:
        _running = False


def start_upstox_feed() -> bool:
    global _thread
    if not UPSTOX_WS_ENABLED or not upstox_configured():
        return False
    if _thread and _thread.is_alive():
        return True
    _thread = threading.Thread(target=_run_streamer, name="upstox-ws", daemon=True)
    _thread.start()
    return True


def stop_upstox_feed() -> None:
    global _streamer, _running
    if _streamer is not None:
        try:
            _streamer.disconnect()
        except Exception:
            logger.debug("Upstox WS disconnect", exc_info=True)
    _running = False
    _streamer = None
