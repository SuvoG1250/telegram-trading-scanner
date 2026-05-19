"""Fyers API v3 — Nifty index option chain & live premium (LTP)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import requests

from config import (
    FYERS_ACCESS_TOKEN,
    FYERS_APP_ID,
    FYERS_NIFTY_INDEX_SYMBOL,
    FYERS_OPTION_STRIKE_COUNT,
)

logger = logging.getLogger(__name__)

FYERS_DATA_BASE = "https://api-t1.fyers.in/data"

_MONTH_ABBREV = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


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


def fyers_configured() -> bool:
    return bool(FYERS_APP_ID and FYERS_ACCESS_TOKEN)


def _headers() -> dict[str, str]:
    app = FYERS_APP_ID.strip()
    tok = FYERS_ACCESS_TOKEN.strip()
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    return {
        "Authorization": f"{app}:{tok}",
        "Content-Type": "application/json",
        "version": "3",
    }


def _get(path: str, params: dict[str, str]) -> dict[str, Any] | None:
    if not fyers_configured():
        return None
    try:
        resp = requests.get(
            f"{FYERS_DATA_BASE}{path}",
            headers=_headers(),
            params=params,
            timeout=30,
        )
        if not resp.ok:
            logger.warning("Fyers %s HTTP %s: %s", path, resp.status_code, resp.text[:400])
            return None
        body = resp.json()
        if isinstance(body, dict) and body.get("s") == "error":
            logger.warning("Fyers %s: %s", path, body)
            return None
        return body
    except requests.RequestException:
        logger.exception("Fyers request failed: %s", path)
        return None


def _nifty_weekly_expiry_date(ref: datetime | None = None) -> date:
    """Next Nifty weekly expiry (Tuesday), aligned with nifty_options._weekly_expiry_label."""
    from market_time import now_ist

    dt = ref or now_ist()
    tuesday = 1
    days = (tuesday - dt.weekday()) % 7
    if days == 0 and dt.hour >= 15 and dt.minute >= 30:
        days = 7
    return (dt + timedelta(days=days)).date()


def _expiry_yymmdd(expiry: date) -> tuple[str, str, str]:
    yy = str(expiry.year)[-2:]
    mmm = _MONTH_ABBREV[expiry.month - 1]
    dd = f"{expiry.day:02d}"
    return yy, mmm, dd


def build_fyers_nifty_option_symbol(expiry: date, strike: int, option_type: str) -> str:
    """
    Fyers NSE Nifty option ticker: NSE:NIFTY + YY + MMM + DD + strike + CE|PE.
    Example style: NSE:NIFTY24JAN2322000CE (23 Jan 2024, 22000 CE).
    """
    yy, mmm, dd = _expiry_yymmdd(expiry)
    ot = option_type.upper().strip()
    if ot not in ("CE", "PE"):
        ot = "CE"
    return f"NSE:NIFTY{yy}{mmm}{dd}{int(strike)}{ot}"


def _ltp_from_leg(leg: dict[str, Any] | None) -> tuple[float, float | None, float | None]:
    if not leg:
        return 0.0, None, None
    md = leg.get("market_data") or leg.get("v") or {}
    if isinstance(md, dict):
        ltp = float(md.get("ltp") or md.get("lp") or md.get("last_price") or 0)
        bid = md.get("bid")
        ask = md.get("ask")
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
        if ltp <= 0:
            ltp = float(md.get("bid_price") or md.get("ask_price") or 0)
        return ltp, bid_f, ask_f
    ltp = float(leg.get("ltp") or leg.get("lp") or 0)
    return ltp, None, None


def _walk_find_strike_rows(obj: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            out.extend(_walk_find_strike_rows(item))
    elif isinstance(obj, dict):
        sp = obj.get("strike_price", obj.get("strike", obj.get("strikePrice")))
        if sp is not None and ("call_options" in obj or "put_options" in obj or "call" in obj or "put" in obj):
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (list, dict)):
                out.extend(_walk_find_strike_rows(v))
    return out


def _find_strike_row(chain_data: Any, strike: int) -> dict[str, Any] | None:
    for row in _walk_find_strike_rows(chain_data):
        sp = row.get("strike_price", row.get("strike", row.get("strikePrice")))
        if sp is not None and int(float(sp)) == int(strike):
            return row
    return None


def _parse_chain_for_strike(
    body: dict[str, Any],
    strike: int,
    option_type: str,
) -> tuple[OptionQuote | None, str | None]:
    raw = body.get("data")
    if raw is None:
        raw = body.get("d")
    spot = None
    if isinstance(raw, dict):
        for key in ("ltp", "last_price", "underlyingValue", "spot_price", "index_ltp"):
            sp = raw.get(key)
            if sp is not None:
                try:
                    spot = float(sp)
                    break
                except (TypeError, ValueError):
                    pass
        row = _find_strike_row(raw, strike)
    else:
        row = _find_strike_row(raw, strike)

    if not row:
        return None, None

    ot = option_type.upper()
    leg_key = "call_options" if ot == "CE" else "put_options"
    leg = row.get(leg_key) or row.get("call" if ot == "CE" else "put")
    if isinstance(leg, str):
        return None, None
    ltp, bid, ask = _ltp_from_leg(leg if isinstance(leg, dict) else None)
    if ltp <= 0:
        return None, None

    exp_raw = row.get("expiry") or row.get("expiry_date") or row.get("date")
    expiry_str = ""
    if isinstance(exp_raw, (int, float)) and exp_raw > 1_000_000_000:
        from datetime import timezone

        expiry_str = datetime.fromtimestamp(int(exp_raw), tz=timezone.utc).strftime("%Y-%m-%d")
    elif isinstance(exp_raw, str):
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
            try:
                expiry_str = datetime.strptime(exp_raw.strip(), fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
    if not expiry_str:
        expiry_str = _nifty_weekly_expiry_date().strftime("%Y-%m-%d")

    sym = ""
    if isinstance(leg, dict):
        sym = str(leg.get("symbol") or leg.get("fy_token") or "")

    return (
        OptionQuote(
            last_price=round(ltp, 2),
            bid=bid,
            ask=ask,
            security_id=None,
            spot=spot,
            expiry=expiry_str,
            strike=int(strike),
            option_type=ot,
        ),
        sym,
    )


def _fetch_quotes_symbol(symbol: str) -> OptionQuote | None:
    body = _get("/quotes", {"symbols": symbol})
    if not body:
        return None
    d = body.get("d") or body.get("data")
    if not d or not isinstance(d, list):
        return None
    first = d[0]
    if not isinstance(first, dict):
        return None
    v = first.get("v") or first
    ltp = float(v.get("lp") or v.get("ltp") or v.get("last_price") or 0)
    if ltp <= 0:
        return None
    bid = v.get("bid")
    ask = v.get("ask")
    return OptionQuote(
        last_price=round(ltp, 2),
        bid=float(bid) if bid is not None else None,
        ask=float(ask) if ask is not None else None,
        security_id=None,
        spot=None,
        expiry=_nifty_weekly_expiry_date().strftime("%Y-%m-%d"),
        strike=int(strike),
        option_type=option_type.upper(),
    )


def fetch_nifty_option_quote(
    strike: int,
    option_type: str,
    expiry: str | None = None,
) -> OptionQuote | None:
    """
    Live LTP for Nifty index option (nearest weekly chain by default).
    expiry: YYYY-MM-DD if known; else uses next Tuesday weekly expiry for symbol fallback.
    """
    if not fyers_configured():
        return None

    exp_date: date
    if expiry:
        try:
            exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        except ValueError:
            exp_date = _nifty_weekly_expiry_date()
    else:
        exp_date = _nifty_weekly_expiry_date()

    params: dict[str, str] = {
        "symbol": FYERS_NIFTY_INDEX_SYMBOL,
        "strikecount": str(FYERS_OPTION_STRIKE_COUNT),
        "timestamp": "",
    }

    body = _get("/options-chain-v3", params)
    if body:
        quote, _ = _parse_chain_for_strike(body, strike, option_type)
        if quote:
            return quote
        logger.debug("Fyers chain parse miss for strike %s %s; keys=%s", strike, option_type, list(body)[:8])

    sym = build_fyers_nifty_option_symbol(exp_date, strike, option_type)
    q = _fetch_quotes_symbol(sym)
    if q:
        q = OptionQuote(
            last_price=q.last_price,
            bid=q.bid,
            ask=q.ask,
            security_id=None,
            spot=q.spot,
            expiry=exp_date.strftime("%Y-%m-%d"),
            strike=int(strike),
            option_type=option_type.upper(),
        )
    return q


def verify_fyers() -> bool:
    if not fyers_configured():
        return False
    body = _get("/quotes", {"symbols": FYERS_NIFTY_INDEX_SYMBOL})
    if not body:
        return False
    d = body.get("d") or body.get("data")
    return bool(d)
