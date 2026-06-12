"""Unified live index option quotes — Fyers preferred, then Upstox, then Dhan."""

from __future__ import annotations

from dataclasses import replace

from config import OPTION_DATA_PROVIDER


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
        from upstox_websocket import get_ws_ltp, subscribe_instruments

        if upstox_configured():
            q = upstox_quote(strike, option_type, expiry)
            if q:
                if q.instrument_key:
                    subscribe_instruments([q.instrument_key])
                    ws_ltp = get_ws_ltp(q.instrument_key)
                    if ws_ltp and ws_ltp > 0:
                        q = replace(q, last_price=round(ws_ltp, 2))
                return q, "upstox"

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
        from upstox_websocket import get_ws_ltp, subscribe_instruments

        if upstox_configured():
            q = upstox_quote(strike, option_type, expiry)
            if q:
                if q.instrument_key:
                    subscribe_instruments([q.instrument_key])
                    ws_ltp = get_ws_ltp(q.instrument_key)
                    if ws_ltp and ws_ltp > 0:
                        q = replace(q, last_price=round(ws_ltp, 2))
                return q, "upstox"

    return None, "estimate"
