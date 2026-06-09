"""Upstox REST API v2 — quotes, option chain, orders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from config import (
    UPSTOX_ACCESS_TOKEN,
    UPSTOX_NIFTY_INSTRUMENT_KEY,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
)

logger = logging.getLogger(__name__)

UPSTOX_BASE = "https://api.upstox.com/v2"
UPSTOX_HFT_BASE = "https://api-hft.upstox.com/v3"


@dataclass
class OptionQuote:
    last_price: float
    bid: float | None
    ask: float | None
    security_id: int | None
    spot: float | None
    expiry: str
    strike: int
    option_type: str
    instrument_key: str = ""


def upstox_configured() -> bool:
    return bool(UPSTOX_ACCESS_TOKEN)


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}",
    }


def _request(method: str, url: str, *, params: dict | None = None, json_body: dict | None = None) -> Any:
    if not upstox_configured():
        return None
    try:
        resp = requests.request(
            method,
            url,
            headers=_headers(),
            params=params or {},
            json=json_body,
            timeout=30,
        )
        if not resp.ok:
            logger.warning("Upstox %s %s HTTP %s: %s", method, url, resp.status_code, resp.text[:400])
            return None
        body = resp.json()
        if isinstance(body, dict) and body.get("status") not in (None, "success"):
            logger.warning("Upstox %s %s: %s", method, url, body)
            return None
        return body.get("data") if isinstance(body, dict) else body
    except requests.RequestException:
        logger.exception("Upstox request failed: %s %s", method, url)
        return None


def _get(path: str, params: dict | None = None) -> dict | list | None:
    data = _request("GET", f"{UPSTOX_BASE}{path}", params=params)
    return data


def _post_v2(path: str, payload: dict) -> dict | None:
    data = _request("POST", f"{UPSTOX_BASE}{path}", json_body=payload)
    return data if isinstance(data, dict) else None


def _post_v3(path: str, payload: dict) -> dict | None:
    data = _request("POST", f"{UPSTOX_HFT_BASE}{path}", json_body=payload)
    return data if isinstance(data, dict) else None


def fetch_expiries(instrument_key: str | None = None) -> list[str]:
    key = instrument_key or UPSTOX_NIFTY_INSTRUMENT_KEY
    data = _get("/option/contract", {"instrument_key": key})
    if not data:
        return []
    return sorted({str(row["expiry"]) for row in data if row.get("expiry")})


def nearest_expiry(expiries: list[str]) -> str | None:
    if not expiries:
        return None
    today = datetime.now().date()
    future: list[tuple] = []
    for raw in expiries:
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= today:
            future.append((d, raw))
    if not future:
        return expiries[-1]
    future.sort(key=lambda x: x[0])
    for d, raw in future:
        if (d - today).days <= 8:
            return raw
    return future[0][1]


def fetch_option_chain(expiry: str, instrument_key: str | None = None) -> list[dict] | None:
    key = instrument_key or UPSTOX_NIFTY_INSTRUMENT_KEY
    data = _get("/option/chain", {"instrument_key": key, "expiry_date": expiry})
    if isinstance(data, list):
        return data
    return None


def verify_upstox() -> bool:
    return len(fetch_expiries()) > 0


def lookup_option_leg(
    *,
    strike: int,
    option_type: str,
    index_instrument_key: str,
    expiry: str | None = None,
) -> tuple[OptionQuote | None, str]:
    """Return quote + instrument_key for option leg."""
    exp = expiry or nearest_expiry(fetch_expiries(index_instrument_key))
    if not exp:
        return None, ""
    chain = fetch_option_chain(exp, index_instrument_key)
    if not chain:
        return None, ""

    row_match = None
    spot = None
    for row in chain:
        sp = row.get("strike_price")
        if sp is not None and int(float(sp)) == int(strike):
            row_match = row
            spot = row.get("underlying_spot_price")
            break
    if not row_match:
        return None, ""

    leg_key = "call_options" if option_type.upper() == "CE" else "put_options"
    leg = row_match.get(leg_key) or {}
    inst_key = str(leg.get("instrument_key") or leg.get("instrument_token") or "")
    md = leg.get("market_data") or {}
    ltp = float(md.get("ltp") or 0)
    if ltp <= 0:
        ltp = float(md.get("ask_price") or md.get("bid_price") or 0)
    if ltp <= 0:
        return None, inst_key

    quote = OptionQuote(
        last_price=round(ltp, 2),
        bid=float(md["bid_price"]) if md.get("bid_price") else None,
        ask=float(md["ask_price"]) if md.get("ask_price") else None,
        security_id=None,
        spot=float(spot) if spot else None,
        expiry=exp,
        strike=strike,
        option_type=option_type.upper(),
        instrument_key=inst_key,
    )
    return quote, inst_key


def _fetch_index_option_quote(
    strike: int,
    option_type: str,
    instrument_key: str,
    expiry: str | None = None,
) -> OptionQuote | None:
    quote, _ = lookup_option_leg(
        strike=strike,
        option_type=option_type,
        index_instrument_key=instrument_key,
        expiry=expiry,
    )
    return quote


def fetch_nifty_option_quote(
    strike: int,
    option_type: str,
    expiry: str | None = None,
) -> OptionQuote | None:
    return _fetch_index_option_quote(strike, option_type, UPSTOX_NIFTY_INSTRUMENT_KEY, expiry)


def fetch_sensex_option_quote(
    strike: int,
    option_type: str,
    expiry: str | None = None,
) -> OptionQuote | None:
    return _fetch_index_option_quote(strike, option_type, UPSTOX_SENSEX_INSTRUMENT_KEY, expiry)


def place_order_v3(payload: dict) -> dict | None:
    return _post_v3("/order/place", payload)


def place_order_v2(payload: dict) -> dict | None:
    return _post_v2("/order/place", payload)
