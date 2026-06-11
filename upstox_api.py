"""Upstox REST API v2 — quotes, option chain, orders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from config import (
    UPSTOX_NIFTY_INSTRUMENT_KEY,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
)
from upstox_token import get_access_token

logger = logging.getLogger(__name__)

UPSTOX_BASE = "https://api.upstox.com/v2"
UPSTOX_HFT_BASE = "https://api-hft.upstox.com/v3"

_LAST_ERROR = ""


def last_upstox_error() -> str:
    """Human-readable detail from the most recent failed Upstox API call."""
    return _LAST_ERROR


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
    return bool(get_access_token())


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_access_token()}",
    }


def _extract_error_message(body: dict | None, fallback: str) -> str:
    if not isinstance(body, dict):
        return fallback
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        parts = []
        for err in errors:
            if isinstance(err, dict):
                msg = err.get("message") or err.get("errorCode") or err.get("error_code")
                if msg:
                    parts.append(str(msg))
            elif err:
                parts.append(str(err))
        if parts:
            return "; ".join(parts)
    for key in ("message", "error", "error_message"):
        if body.get(key):
            return str(body[key])
    return fallback


def _request(method: str, url: str, *, params: dict | None = None, json_body: dict | None = None) -> Any:
    global _LAST_ERROR
    if not upstox_configured():
        _LAST_ERROR = "UPSTOX_ACCESS_TOKEN not set"
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
        body: dict | None = None
        try:
            body = resp.json() if resp.text else None
        except ValueError:
            body = None
        if not resp.ok:
            detail = _extract_error_message(body, resp.text[:400] if resp.text else f"HTTP {resp.status_code}")
            _LAST_ERROR = detail
            logger.warning("Upstox %s %s HTTP %s: %s", method, url, resp.status_code, detail)
            return None
        if isinstance(body, dict) and body.get("status") not in (None, "success"):
            detail = _extract_error_message(body, str(body)[:400])
            _LAST_ERROR = detail
            logger.warning("Upstox %s %s: %s", method, url, detail)
            return None
        _LAST_ERROR = ""
        return body.get("data") if isinstance(body, dict) else body
    except requests.RequestException as exc:
        _LAST_ERROR = str(exc)
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


def verify_upstox_trading() -> tuple[bool, str]:
    """Quotes work with read-only tokens; profile needs a trading token."""
    if not upstox_configured():
        return False, "No token"
    data = _get("/user/profile")
    if data:
        return True, "Trading token OK"
    err = (last_upstox_error() or "").lower()
    if "read only" in err or "read-only" in err:
        return False, "Read-only token — use app Generate → /upstox_token (not Analytics tab)"
    if err:
        return False, last_upstox_error()
    return False, "Could not verify trading access"


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


def parse_order_ids(data: dict | None) -> list[str]:
    """V3 returns order_ids[]; V2 returns order_id."""
    if not data:
        return []
    raw_ids = data.get("order_ids")
    if isinstance(raw_ids, list):
        return [str(x) for x in raw_ids if x]
    oid = data.get("order_id") or data.get("orderId")
    return [str(oid)] if oid else []


def place_order_v3(payload: dict) -> dict | None:
    return _post_v3("/order/place", payload)


def place_order_v2(payload: dict) -> dict | None:
    return _post_v2("/order/place", payload)


def place_order(payload: dict) -> tuple[list[str], dict | None]:
    """Place order via V3; fall back to V2. Returns (order_ids, raw_data)."""
    data = place_order_v3(payload)
    ids = parse_order_ids(data)
    if ids:
        return ids, data
    data_v2 = place_order_v2(payload)
    ids_v2 = parse_order_ids(data_v2)
    if ids_v2:
        return ids_v2, data_v2
    return [], data
