"""Dhan HQ v2 — Nifty option chain & live premium (LTP)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import requests

from config import (
    DHAN_ACCESS_TOKEN,
    DHAN_CLIENT_ID,
    DHAN_SANDBOX,
    NIFTY_UNDERLYING_SCRIP,
    NIFTY_UNDERLYING_SEG,
)

logger = logging.getLogger(__name__)

DHAN_BASE_LIVE = "https://api.dhan.co/v2"
DHAN_BASE_SANDBOX = "https://sandbox.dhan.co/v2"
_LAST_CHAIN_AT = 0.0
_CHAIN_MIN_INTERVAL = 3.1


def dhan_base_url() -> str:
    return DHAN_BASE_SANDBOX if DHAN_SANDBOX else DHAN_BASE_LIVE


def dhan_option_chain_available() -> bool:
    """Live option chain requires production token + Data API plan."""
    return dhan_configured() and not DHAN_SANDBOX


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


def dhan_configured() -> bool:
    return bool(DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID)


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": DHAN_ACCESS_TOKEN,
        "client-id": DHAN_CLIENT_ID,
    }


def _post(path: str, payload: dict) -> dict | None:
    if not dhan_configured():
        return None
    try:
        resp = requests.post(
            f"{dhan_base_url()}{path}",
            headers=_headers(),
            json=payload,
            timeout=30,
        )
        if not resp.ok:
            logger.warning("Dhan %s HTTP %s: %s", path, resp.status_code, resp.text[:300])
            return None
        body = resp.json()
        if body.get("status") != "success":
            logger.warning("Dhan %s status: %s", path, body)
            return None
        return body.get("data")
    except requests.RequestException:
        logger.exception("Dhan request failed: %s", path)
        return None


def fetch_expiry_list() -> list[str]:
    data = _post(
        "/optionchain/expirylist",
        {
            "UnderlyingScrip": NIFTY_UNDERLYING_SCRIP,
            "UnderlyingSeg": NIFTY_UNDERLYING_SEG,
        },
    )
    if not data:
        return []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def _nearest_expiry(expiry_list: list[str], prefer_weekly: bool = True) -> str | None:
    if not expiry_list:
        return None
    today = datetime.now().date()
    future = []
    for raw in expiry_list:
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= today:
            future.append((d, raw))
    if not future:
        return expiry_list[-1]
    future.sort(key=lambda x: x[0])
    if not prefer_weekly:
        return future[0][1]
    # Prefer nearest expiry within ~8 days (weekly); else nearest monthly
    for d, raw in future:
        if (d - today).days <= 8:
            return raw
    return future[0][1]


def fetch_option_chain(expiry: str) -> dict | None:
    global _LAST_CHAIN_AT
    wait = _CHAIN_MIN_INTERVAL - (time.time() - _LAST_CHAIN_AT)
    if wait > 0:
        time.sleep(wait)
    data = _post(
        "/optionchain",
        {
            "UnderlyingScrip": NIFTY_UNDERLYING_SCRIP,
            "UnderlyingSeg": NIFTY_UNDERLYING_SEG,
            "Expiry": expiry,
        },
    )
    _LAST_CHAIN_AT = time.time()
    return data


def _strike_key(strike: int, oc: dict) -> str | None:
    for key in oc:
        try:
            if int(float(key)) == strike:
                return key
        except (TypeError, ValueError):
            continue
    return None


def verify_dhan_profile() -> dict | None:
    """Quick auth + Data API plan check (GET /profile)."""
    if not dhan_configured():
        return None
    try:
        resp = requests.get(
            f"{dhan_base_url()}/profile",
            headers=_headers(),
            timeout=20,
        )
        if not resp.ok:
            logger.warning("Dhan profile HTTP %s: %s", resp.status_code, resp.text[:300])
            return None
        return resp.json()
    except requests.RequestException:
        logger.exception("Dhan profile request failed")
        return None


def fetch_nifty_option_quote(strike: int, option_type: str, expiry: str | None = None) -> OptionQuote | None:
    """
    Live CE/PE premium from Dhan option chain.
    option_type: 'CE' or 'PE'
    """
    if not dhan_configured():
        return None

    if not dhan_option_chain_available():
        return None

    exp = expiry
    if not exp:
        expiries = fetch_expiry_list()
        exp = _nearest_expiry(expiries)
    if not exp:
        logger.warning("Dhan: no expiry available for Nifty.")
        return None

    chain = fetch_option_chain(exp)
    if not chain or "oc" not in chain:
        return None

    oc = chain["oc"]
    key = _strike_key(strike, oc)
    if not key:
        logger.warning("Dhan: strike %s not in chain for %s.", strike, exp)
        return None

    leg = oc[key].get("ce" if option_type.upper() == "CE" else "pe")
    if not leg:
        return None

    ltp = float(leg.get("last_price") or 0)
    if ltp <= 0:
        ltp = float(leg.get("top_ask_price") or leg.get("top_bid_price") or 0)
    if ltp <= 0:
        return None

    return OptionQuote(
        last_price=round(ltp, 2),
        bid=float(leg["top_bid_price"]) if leg.get("top_bid_price") else None,
        ask=float(leg["top_ask_price"]) if leg.get("top_ask_price") else None,
        security_id=int(leg["security_id"]) if leg.get("security_id") else None,
        spot=float(chain.get("last_price")) if chain.get("last_price") else None,
        expiry=exp,
        strike=strike,
        option_type=option_type.upper(),
    )
