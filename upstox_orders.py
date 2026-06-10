"""Place Upstox orders — entry + SL + target from scanner signals."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    DATA_DIR,
    UPSTOX_NIFTY_INSTRUMENT_KEY,
    UPSTOX_PRODUCT_OPTION,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
    upstox_nifty_lot_size,
    upstox_sensex_lot_size,
)
from upstox_trade_state import auto_trade_enabled, get_lots, paper_trade
from market_time import today_key
from telegram_client import Signal
from upstox_api import last_upstox_error, lookup_option_leg, place_order, upstox_configured
from upstox_websocket import subscribe_instruments

logger = logging.getLogger(__name__)

_ORDERS_FILE = DATA_DIR / "upstox_orders.json"


@dataclass
class OrderResult:
    ok: bool
    tag: str
    order_ids: list[str]
    message: str
    paper: bool = False


def _load_log() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _ORDERS_FILE.exists():
        return {"date": today_key(), "orders": []}
    try:
        return json.loads(_ORDERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": today_key(), "orders": []}


def _save_log(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ORDERS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _record(tag: str, payload: dict, result: OrderResult) -> None:
    data = _load_log()
    if data.get("date") != today_key():
        data = {"date": today_key(), "orders": []}
    data["orders"].append(
        {
            "tag": tag,
            "payload": payload,
            "ok": result.ok,
            "order_ids": result.order_ids,
            "message": result.message,
            "paper": result.paper,
        }
    )
    _save_log(data)


def _parse_expiry_label(label: str | None) -> str | None:
    if not label:
        return None
    try:
        return datetime.strptime(label.strip(), "%d %b %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _place(
    *,
    instrument_key: str,
    transaction_type: str,
    order_type: str,
    quantity: int,
    product: str,
    price: float = 0.0,
    trigger_price: float = 0.0,
    tag: str,
) -> str | None:
    payload = {
        "quantity": quantity,
        "product": product,
        "validity": "DAY",
        "price": round(price, 2),
        "tag": tag[:20],
        "instrument_token": instrument_key,
        "order_type": order_type,
        "transaction_type": transaction_type,
        "disclosed_quantity": 0,
        "trigger_price": round(trigger_price, 2),
        "is_amo": False,
        "slice": True,
    }
    if order_type in ("MARKET", "SL-M"):
        payload["market_protection"] = -1
    if paper_trade():
        logger.info("PAPER Upstox order: %s", payload)
        return f"PAPER-{tag}"

    order_ids, _data = place_order(payload)
    if not order_ids:
        return None
    return order_ids[0]


def _option_context(signal: Signal) -> tuple[str, int, str] | None:
    strike = int(signal.strike or 0)
    opt = (signal.option_type or "CE").upper()
    if strike <= 0:
        return None
    if signal.instrument == "SENSEX_OPTION":
        index_key = UPSTOX_SENSEX_INSTRUMENT_KEY
        lot = upstox_sensex_lot_size()
    else:
        index_key = UPSTOX_NIFTY_INSTRUMENT_KEY
        lot = upstox_nifty_lot_size()
    expiry = _parse_expiry_label(signal.expiry_label)
    quote, inst = lookup_option_leg(
        strike=strike,
        option_type=opt,
        index_instrument_key=index_key,
        expiry=expiry,
    )
    if not inst:
        logger.warning("Upstox: no instrument_key for %s %s %s", signal.symbol, strike, opt)
        return None
    subscribe_instruments([inst])
    return inst, lot * get_lots(), UPSTOX_PRODUCT_OPTION


def execute_signal_orders(signal: Signal) -> OrderResult | None:
    """Place entry + SL + target on Upstox — Nifty/Sensex options only (no stocks/BTST)."""
    if not auto_trade_enabled() or not upstox_configured():
        return None
    if signal.instrument not in ("NIFTY_OPTION", "SENSEX_OPTION"):
        return None

    lv = signal.levels
    tag_base = f"tg-{signal.symbol.replace(' ', '-')[:12]}"
    order_ids: list[str] = []

    ctx = _option_context(signal)
    if not ctx:
        return OrderResult(False, tag_base, [], "Could not resolve option instrument_key")
    inst_key, qty, product = ctx

    entry_id = _place(
        instrument_key=inst_key,
        transaction_type="BUY",
        order_type="MARKET",
        quantity=qty,
        product=product,
        tag=f"{tag_base}-entry",
    )
    if not entry_id:
        detail = last_upstox_error() or "Entry order failed"
        res = OrderResult(False, tag_base, [], detail)
        _record(tag_base, {"signal": signal.symbol}, res)
        return res
    order_ids.append(entry_id)

    sl_id = _place(
        instrument_key=inst_key,
        transaction_type="SELL",
        order_type="SL-M",
        quantity=qty,
        product=product,
        trigger_price=float(lv.stop_loss),
        tag=f"{tag_base}-sl",
    )
    if sl_id:
        order_ids.append(sl_id)

    tgt_id = _place(
        instrument_key=inst_key,
        transaction_type="SELL",
        order_type="LIMIT",
        quantity=qty,
        product=product,
        price=float(lv.primary_target),
        tag=f"{tag_base}-tgt",
    )
    if tgt_id:
        order_ids.append(tgt_id)

    is_paper = paper_trade()
    mode = "PAPER" if is_paper else "LIVE"
    legs = ["entry"]
    if sl_id:
        legs.append(f"SL-M @ {lv.stop_loss:.2f}")
    else:
        legs.append("SL-M failed")
    if tgt_id:
        legs.append(f"LIMIT @ {lv.primary_target:.2f}")
    else:
        legs.append("target LIMIT failed")
    res = OrderResult(
        True,
        tag_base,
        order_ids,
        f"{mode} option: {' + '.join(legs)}",
        paper=is_paper,
    )
    _record(tag_base, {"instrument_key": inst_key, "qty": qty}, res)
    return res
