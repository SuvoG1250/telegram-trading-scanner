"""Upstox API v2 — Nifty option chain & live premium."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import requests

from config import UPSTOX_ACCESS_TOKEN, UPSTOX_NIFTY_INSTRUMENT_KEY

logger = logging.getLogger(__name__)

UPSTOX_BASE = "https://api.upstox.com/v2"


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


def upstox_configured() -> bool:
    return bool(UPSTOX_ACCESS_TOKEN)


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}",
    }


def _get(path: str, params: dict | None = None) -> dict | list | None:
    if not upstox_configured():
        return None
    try:
        resp = requests.get(
            f"{UPSTOX_BASE}{path}",
            headers=_headers(),
            params=params or {},
            timeout=30,
        )
        if not resp.ok:
            logger.warning("Upstox %s HTTP %s: %s", path, resp.status_code, resp.text[:300])
            return None
        body = resp.json()
        if body.get("status") != "success":
            logger.warning("Upstox %s: %s", path, body)
            return None
        return body.get("data")
    except requests.RequestException:
        logger.exception("Upstox request failed: %s", path)
        return None


def fetch_expiries() -> list[str]:
    data = _get(
        "/option/contract",
        {"instrument_key": UPSTOX_NIFTY_INSTRUMENT_KEY},
    )
    if not data:
        return []
    return sorted({str(row["expiry"]) for row in data if row.get("expiry")})


def _nearest_expiry(expiries: list[str]) -> str | None:
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


def fetch_option_chain(expiry: str) -> list[dict] | None:
    data = _get(
        "/option/chain",
        {
            "instrument_key": UPSTOX_NIFTY_INSTRUMENT_KEY,
            "expiry_date": expiry,
        },
    )
    if isinstance(data, list):
        return data
    return None


def verify_upstox() -> bool:
    return len(fetch_expiries()) > 0


def fetch_nifty_option_quote(
    strike: int,
    option_type: str,
    expiry: str | None = None,
) -> OptionQuote | None:
    if not upstox_configured():
        return None

    exp = expiry or _nearest_expiry(fetch_expiries())
    if not exp:
        return None

    chain = fetch_option_chain(exp)
    if not chain:
        return None

    row_match = None
    spot = None
    for row in chain:
        sp = row.get("strike_price")
        if sp is not None and int(float(sp)) == int(strike):
            row_match = row
            spot = row.get("underlying_spot_price")
            break

    if not row_match:
        logger.warning("Upstox: strike %s not in chain for %s.", strike, exp)
        return None

    leg_key = "call_options" if option_type.upper() == "CE" else "put_options"
    leg = row_match.get(leg_key) or {}
    md = leg.get("market_data") or {}
    ltp = float(md.get("ltp") or 0)
    if ltp <= 0:
        ltp = float(md.get("ask_price") or md.get("bid_price") or 0)
    if ltp <= 0:
        return None

    return OptionQuote(
        last_price=round(ltp, 2),
        bid=float(md["bid_price"]) if md.get("bid_price") else None,
        ask=float(md["ask_price"]) if md.get("ask_price") else None,
        security_id=None,
        spot=float(spot) if spot else None,
        expiry=exp,
        strike=strike,
        option_type=option_type.upper(),
    )
