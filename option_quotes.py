"""Unified live index option quotes — Fyers preferred, then Upstox, then Dhan."""

from __future__ import annotations

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
        from upstox_client import fetch_nifty_option_quote as upstox_quote
        from upstox_client import upstox_configured

        if upstox_configured():
            q = upstox_quote(strike, option_type, expiry)
            if q:
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
        from upstox_client import fetch_sensex_option_quote as upstox_quote
        from upstox_client import upstox_configured

        if upstox_configured():
            q = upstox_quote(strike, option_type, expiry)
            if q:
                return q, "upstox"

    return None, "estimate"
