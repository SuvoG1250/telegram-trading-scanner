"""Unified live index option quotes — Upstox live WS + v3 LTP (default)."""

from __future__ import annotations

import time
from dataclasses import replace

from config import OPTION_DATA_PROVIDER, UPSTOX_WS_WAIT_SEC


def _finalize_upstox_quote(quote):
    """Subscribe leg and prefer realtime WS LTP, then v3 REST refresh."""
    if not quote or not quote.instrument_key:
        return quote
    from upstox_api import refresh_option_ltp
    from upstox_websocket import get_ws_ltp, subscribe_instruments

    subscribe_instruments([quote.instrument_key])
    deadline = time.monotonic() + max(0.5, UPSTOX_WS_WAIT_SEC)
    while time.monotonic() < deadline:
        ws_ltp = get_ws_ltp(quote.instrument_key)
        if ws_ltp and ws_ltp > 0:
            return replace(quote, last_price=round(ws_ltp, 2))
        time.sleep(0.2)
    return refresh_option_ltp(quote)


def fetch_nifty_option_quote(strike: int, option_type: str, expiry: str | None = None):
    provider = OPTION_DATA_PROVIDER.lower()

    if provider in ("fyers", "auto"):
        from fyers_client import fetch_nifty_option_quote as fyers_quote
        from fyers_client import fyers_configured

        if fyers_configured():
            q = fyers_quote(strike, option_type, expiry)
            if q:
                return q, "fyers"

    if provider in ("upstox", "auto"):
        from upstox_api import fetch_nifty_option_quote as upstox_quote
        from upstox_api import upstox_configured

        if upstox_configured():
            q = upstox_quote(strike, option_type, expiry)
            if q:
                return _finalize_upstox_quote(q), "upstox"

    if provider in ("dhan", "auto"):
        from dhan_client import dhan_option_chain_available, fetch_nifty_option_quote as dhan_quote

        if dhan_option_chain_available():
            q = dhan_quote(strike, option_type, expiry)
            if q:
                return q, "dhan"

    return None, "estimate"


def fetch_sensex_option_quote(strike: int, option_type: str, expiry: str | None = None):
    provider = OPTION_DATA_PROVIDER.lower()

    if provider in ("fyers", "auto"):
        from fyers_client import fetch_sensex_option_quote as fyers_quote
        from fyers_client import fyers_configured

        if fyers_configured():
            q = fyers_quote(strike, option_type, expiry)
            if q:
                return q, "fyers"

    if provider in ("upstox", "auto"):
        from upstox_api import fetch_sensex_option_quote as upstox_quote
        from upstox_api import upstox_configured

        if upstox_configured():
            q = upstox_quote(strike, option_type, expiry)
            if q:
                return _finalize_upstox_quote(q), "upstox"

    if provider in ("dhan", "auto"):
        from dhan_client import dhan_option_chain_available, fetch_sensex_option_quote as dhan_quote

        if dhan_option_chain_available():
            q = dhan_quote(strike, option_type, expiry)
            if q:
                return q, "dhan"

    return None, "estimate"
