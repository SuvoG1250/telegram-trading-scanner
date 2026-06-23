"""Upstox REST API v2 — quotes, option chain, orders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import requests

from config import (
    UPSTOX_NIFTY_INSTRUMENT_KEY,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
)
from upstox_token import get_access_token

logger = logging.getLogger(__name__)

UPSTOX_BASE = "https://api.upstox.com/v2"
UPSTOX_V3_BASE = "https://api.upstox.com/v3"
UPSTOX_HFT_BASE = "https://api-hft.upstox.com/v3"

# Nifty weekly = Tue (1), Sensex weekly = Thu (3) — same as Fyers / index_options specs.
_INDEX_WEEKLY_WEEKDAY = {
    "NSE_INDEX|Nifty 50": 1,
    "BSE_INDEX|SENSEX": 3,
}
_expiry_cache: dict[tuple[str, str], str] = {}

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


def _weekly_expiry_date(expiry_weekday: int, ref: datetime | None = None) -> date:
    """Next weekly expiry on expiry_weekday (0=Mon … 3=Thu)."""
    from market_time import now_ist

    dt = ref or now_ist()
    days = (expiry_weekday - dt.weekday()) % 7
    if days == 0 and dt.hour >= 15 and dt.minute >= 30:
        days = 7
    return (dt + timedelta(days=days)).date()


def _normalize_instrument_key(key: str) -> str:
    return key.replace(":", "|").strip()


def fetch_ltp_v3(instrument_keys: list[str]) -> dict[str, float]:
    """Live LTP via Upstox market-quote v3 (fresher than option-chain snapshot)."""
    keys = [_normalize_instrument_key(k) for k in instrument_keys if k]
    if not keys or not upstox_configured():
        return {}
    data = _request(
        "GET",
        f"{UPSTOX_V3_BASE}/market-quote/ltp",
        params={"instrument_key": ",".join(keys)},
    )
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for raw_key, payload in data.items():
        if not isinstance(payload, dict):
            continue
        lp = payload.get("last_price")
        if lp is None:
            continue
        try:
            out[_normalize_instrument_key(str(raw_key))] = float(lp)
        except (TypeError, ValueError):
            pass
    return out


def refresh_option_ltp(quote: OptionQuote) -> OptionQuote:
    """Prefer v3 LTP over stale option-chain snapshot."""
    if not quote.instrument_key:
        return quote
    v3 = fetch_ltp_v3([quote.instrument_key])
    ltp = v3.get(_normalize_instrument_key(quote.instrument_key))
    if ltp and ltp > 0:
        return OptionQuote(
            last_price=round(ltp, 2),
            bid=quote.bid,
            ask=quote.ask,
            security_id=quote.security_id,
            spot=quote.spot,
            expiry=quote.expiry,
            strike=quote.strike,
            option_type=quote.option_type,
            instrument_key=quote.instrument_key,
        )
    return quote


def nearest_expiry(expiries: list[str]) -> str | None:
    if not expiries:
        return None
    from market_time import now_ist

    today = now_ist().date()
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
    return future[0][1]


def resolve_option_expiry(
    index_instrument_key: str,
    expiry: str | None = None,
) -> str | None:
    """Pick expiry with chain data — weekly first, then earliest listed."""
    from market_time import now_ist

    today = now_ist().date()
    if expiry:
        if fetch_option_chain(expiry, index_instrument_key):
            return expiry
        return None

    cache_key = (index_instrument_key, today.isoformat())
    cached = _expiry_cache.get(cache_key)
    if cached and fetch_option_chain(cached, index_instrument_key):
        return cached
    weekday = _INDEX_WEEKLY_WEEKDAY.get(index_instrument_key)
    if weekday is not None:
        weekly = _weekly_expiry_date(weekday)
        found: list[date] = []
        seen: set[date] = set()
        for week_offset in (-1, 0, 1, 2):
            anchor = weekly + timedelta(weeks=week_offset)
            for delta in (-2, -1, 0, 1, 2):
                d = anchor + timedelta(days=delta)
                if d in seen:
                    continue
                seen.add(d)
                if fetch_option_chain(d.strftime("%Y-%m-%d"), index_instrument_key):
                    found.append(d)
        if found:
            future = sorted(d for d in found if d >= today)
            if future:
                chosen = future[0].strftime("%Y-%m-%d")
                _expiry_cache[cache_key] = chosen
                return chosen
            recent_past = sorted(d for d in found if 0 < (today - d).days <= 7)
            if recent_past:
                chosen = recent_past[-1].strftime("%Y-%m-%d")
                _expiry_cache[cache_key] = chosen
                return chosen
            best = min(found, key=lambda d: abs((d - today).days))
            chosen = best.strftime("%Y-%m-%d")
            _expiry_cache[cache_key] = chosen
            return chosen

    expiries = fetch_expiries(index_instrument_key)
    exp = nearest_expiry(expiries)
    if exp and fetch_option_chain(exp, index_instrument_key):
        _expiry_cache[cache_key] = exp
        return exp
    if exp:
        _expiry_cache[cache_key] = exp
    return exp


def _find_chain_row(chain: list[dict], strike: int) -> dict | None:
    best_row = None
    best_diff: int | None = None
    for row in chain:
        sp = row.get("strike_price")
        if sp is None:
            continue
        s = int(float(sp))
        diff = abs(s - strike)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_row = row
            if diff == 0:
                break
    return best_row


def _ltp_from_leg_md(md: dict) -> float:
    ltp = float(md.get("ltp") or 0)
    if ltp > 0:
        return ltp
    bid = float(md.get("bid_price") or 0)
    ask = float(md.get("ask_price") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return bid or ask or 0.0


def fetch_option_chain(expiry: str, instrument_key: str | None = None) -> list[dict] | None:
    key = instrument_key or UPSTOX_NIFTY_INSTRUMENT_KEY
    data = _get("/option/chain", {"instrument_key": key, "expiry_date": expiry})
    if isinstance(data, list):
        return data
    return None


def verify_upstox() -> bool:
    return len(fetch_expiries()) > 0


def verify_upstox_trading() -> tuple[bool, str]:
    """Quotes work with read-only tokens; profile/orders need a trading token."""
    if not upstox_configured():
        return False, "No token"
    from upstox_token import token_is_likely_analytics

    if token_is_likely_analytics():
        return False, "Analytics token (read-only) — quotes OK, orders blocked. Use app Generate token."
    data = _get("/user/profile")
    if data:
        return True, "Trading token OK"
    err = (last_upstox_error() or "").lower()
    if "read only" in err or "read-only" in err:
        return False, "Read-only token — use app Generate → /upstox_token (not Analytics tab)"
    if "static ip" in err:
        return False, "Static IP lock on profile — disable IP whitelist in Upstox app or use OAuth /upstox_login"
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
    exp = resolve_option_expiry(index_instrument_key, expiry)
    if not exp:
        return None, ""
    chain = fetch_option_chain(exp, index_instrument_key)
    if not chain:
        return None, ""

    row_match = _find_chain_row(chain, strike)
    if not row_match:
        return None, ""

    leg_strike = int(float(row_match.get("strike_price") or strike))
    spot = row_match.get("underlying_spot_price")

    leg_key = "call_options" if option_type.upper() == "CE" else "put_options"
    leg = row_match.get(leg_key) or {}
    inst_key = str(leg.get("instrument_key") or leg.get("instrument_token") or "")
    md = leg.get("market_data") or {}
    ltp = _ltp_from_leg_md(md)
    if ltp <= 0 and inst_key:
        v3 = fetch_ltp_v3([inst_key])
        ltp = v3.get(_normalize_instrument_key(inst_key), 0.0)
    if ltp <= 0:
        return None, inst_key

    quote = OptionQuote(
        last_price=round(ltp, 2),
        bid=float(md["bid_price"]) if md.get("bid_price") else None,
        ask=float(md["ask_price"]) if md.get("ask_price") else None,
        security_id=None,
        spot=float(spot) if spot else None,
        expiry=exp,
        strike=leg_strike,
        option_type=option_type.upper(),
        instrument_key=inst_key,
    )
    return refresh_option_ltp(quote), inst_key


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


def place_gtt_order(payload: dict) -> tuple[list[str], dict | None]:
    """Place GTT via POST /v3/order/gtt/place. Returns (gtt_order_ids, raw_data)."""
    global _LAST_ERROR
    if not upstox_configured():
        _LAST_ERROR = "UPSTOX_ACCESS_TOKEN not set"
        return [], None
    body = _request("POST", f"{UPSTOX_V3_BASE}/order/gtt/place", json_body=payload)
    if not isinstance(body, dict):
        return [], None
    ids = [str(x) for x in (body.get("gtt_order_ids") or []) if x]
    return ids, body


def fetch_gtt_orders(gtt_order_id: str | None = None) -> list[dict]:
    """List active GTT orders (or fetch one by id)."""
    params: dict[str, str] = {}
    if gtt_order_id:
        params["gtt_order_id"] = gtt_order_id
    data = _request("GET", f"{UPSTOX_V3_BASE}/order/gtt", params=params or None)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def cancel_gtt_order(gtt_order_id: str) -> bool:
    """Cancel an active GTT order."""
    body = _request(
        "DELETE",
        f"{UPSTOX_V3_BASE}/order/gtt/cancel",
        json_body={"gtt_order_id": gtt_order_id},
    )
    if body is None:
        return False
    if isinstance(body, dict):
        ids = body.get("gtt_order_ids") or body.get("gtt_order_id")
        return bool(ids)
    return True


def fetch_short_term_positions() -> list[dict]:
    """Open intraday/delivery positions from portfolio API."""
    data = _get("/portfolio/short-term-positions")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def cancel_order_v3(order_id: str) -> bool:
    body = _request(
        "DELETE",
        f"{UPSTOX_V3_BASE}/order/cancel",
        json_body={"order_id": order_id},
    )
    return body is not None
