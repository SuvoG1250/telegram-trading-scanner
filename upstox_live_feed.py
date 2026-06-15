"""Bootstrap Upstox WebSocket + REST for live index/option LTP before scans."""

from __future__ import annotations

import logging
import time

from config import (
    NIFTY_STRIKE_STEP,
    SENSEX_STRIKE_STEP,
    UPSTOX_NIFTY_INSTRUMENT_KEY,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
    UPSTOX_WS_WARMUP_SEC,
)
from upstox_api import (
    _normalize_instrument_key,
    fetch_ltp_v3,
    lookup_option_leg,
    upstox_configured,
)
from upstox_websocket import get_ws_ltp, start_upstox_feed, subscribe_instruments

logger = logging.getLogger(__name__)


def _round_strike(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


def _index_spot(index_key: str) -> float | None:
    ltp_map = fetch_ltp_v3([index_key])
    val = ltp_map.get(_normalize_instrument_key(index_key))
    if val and val > 0:
        return float(val)
    ws = get_ws_ltp(index_key)
    return ws if ws and ws > 0 else None


def bootstrap_subscription_keys() -> list[str]:
    """Index + ATM weekly CE/PE legs to keep WS cache warm."""
    keys: list[str] = [UPSTOX_NIFTY_INSTRUMENT_KEY, UPSTOX_SENSEX_INSTRUMENT_KEY]
    if not upstox_configured():
        return keys

    specs = (
        (UPSTOX_NIFTY_INSTRUMENT_KEY, NIFTY_STRIKE_STEP),
        (UPSTOX_SENSEX_INSTRUMENT_KEY, SENSEX_STRIKE_STEP),
    )
    for index_key, step in specs:
        spot = _index_spot(index_key)
        if not spot:
            continue
        strike = _round_strike(spot, step)
        for opt in ("CE", "PE"):
            _quote, inst = lookup_option_leg(
                strike=strike,
                option_type=opt,
                index_instrument_key=index_key,
            )
            if inst:
                keys.append(inst)

    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def warmup_ws_cache(seconds: float | None = None) -> None:
    wait = UPSTOX_WS_WARMUP_SEC if seconds is None else max(0.0, seconds)
    if wait <= 0:
        return
    logger.info("Upstox WS warmup %.1fs for live ticks...", wait)
    time.sleep(wait)


def prepare_live_feed() -> bool:
    """Subscribe index + ATM options, start WS, brief warmup."""
    if not upstox_configured():
        logger.info("Upstox not configured — skip live feed bootstrap.")
        return False
    keys = bootstrap_subscription_keys()
    subscribe_instruments(keys)
    started = start_upstox_feed()
    if started:
        warmup_ws_cache()
        logger.info("Upstox live feed ready (%d instrument(s) subscribed).", len(keys))
    return started


def best_live_ltp(instrument_key: str, *, rest_fallback: float | None = None) -> float | None:
    """Prefer WebSocket LTP; optional REST fallback price."""
    if not instrument_key:
        return rest_fallback
    ws = get_ws_ltp(instrument_key)
    if ws and ws > 0:
        return ws
    v3 = fetch_ltp_v3([instrument_key])
    live = v3.get(_normalize_instrument_key(instrument_key))
    if live and live > 0:
        return live
    return rest_fallback
