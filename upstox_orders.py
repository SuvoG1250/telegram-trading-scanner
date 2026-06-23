"""Place Upstox orders — GTT multi-leg entry + SL + target from scanner signals."""

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
    UPSTOX_USE_GTT,
    upstox_nifty_lot_size,
    upstox_sensex_lot_size,
)
from market_time import today_key
from telegram_client import Signal
from gtt_premium_levels import gtt_points_summary, gtt_prices
from upstox_api import last_upstox_error, lookup_option_leg, place_gtt_order, place_order, upstox_configured, verify_upstox_trading
from upstox_trade_state import auto_trade_enabled, get_lots, paper_trade

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


def list_today_gtt_orders() -> list[dict]:
    """Return today's GTT order log entries with metadata."""
    data = _load_log()
    if data.get("date") != today_key():
        return []
    out: list[dict] = []
    for row in data.get("orders") or []:
        if not row.get("ok"):
            continue
        payload = row.get("payload") or {}
        if not payload.get("gtt"):
            continue
        merged = dict(payload)
        merged["tag"] = row.get("tag")
        merged["order_ids"] = row.get("order_ids") or payload.get("gtt_order_ids") or []
        merged["gtt_order_ids"] = merged["order_ids"]
        merged["paper"] = row.get("paper")
        out.append(merged)
    return out


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


def _place_regular(
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


def _place_gtt_bundle(
    *,
    instrument_key: str,
    quantity: int,
    product: str,
    entry_price: float,
    sl_price: float,
    target_price: float,
    tag: str,
) -> list[str]:
    payload = {
        "type": "MULTIPLE",
        "quantity": quantity,
        "product": product,
        "instrument_token": instrument_key,
        "transaction_type": "BUY",
        "rules": [
            {
                "strategy": "ENTRY",
                "trigger_type": "IMMEDIATE",
                "trigger_price": round(entry_price, 2),
            },
            {
                "strategy": "STOPLOSS",
                "trigger_type": "IMMEDIATE",
                "trigger_price": round(sl_price, 2),
            },
            {
                "strategy": "TARGET",
                "trigger_type": "IMMEDIATE",
                "trigger_price": round(target_price, 2),
            },
        ],
    }
    if paper_trade():
        logger.info("PAPER Upstox GTT: %s", payload)
        return [f"PAPER-GTT-{tag}"]

    ids, _data = place_gtt_order(payload)
    return ids


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
    _quote, inst = lookup_option_leg(
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
    """Place entry + SL + target on Upstox — Nifty/Sensex options only."""
    if not auto_trade_enabled() or not upstox_configured():
        return None
    if signal.instrument not in ("NIFTY_OPTION", "SENSEX_OPTION"):
        return None

    if not paper_trade():
        from upstox_token import token_is_likely_analytics

        if token_is_likely_analytics():
            return OrderResult(
                False,
                "tg-order",
                [],
                "Analytics (read-only) token — use app Generate token via /upstox_token then /live",
            )
        trade_ok, trade_msg = verify_upstox_trading()
        if not trade_ok:
            return OrderResult(False, "tg-order", [], trade_msg or "Trading token not valid for orders")

    lv = signal.levels
    tag_base = f"tg-{signal.symbol.replace(' ', '-')[:12]}"
    alert_premium = float(lv.entry)
    entry_price, sl_price, target_price = gtt_prices(alert_premium, signal.instrument or "NIFTY_OPTION")
    gtt_summary = gtt_points_summary(signal.instrument or "NIFTY_OPTION")

    ctx = _option_context(signal)
    if not ctx:
        return OrderResult(False, tag_base, [], "Could not resolve option instrument_key")
    inst_key, qty, product = ctx

    if UPSTOX_USE_GTT:
        gtt_ids = _place_gtt_bundle(
            instrument_key=inst_key,
            quantity=qty,
            product=product,
            entry_price=entry_price,
            sl_price=sl_price,
            target_price=target_price,
            tag=tag_base,
        )
        if not gtt_ids:
            detail = last_upstox_error() or "GTT order failed"
            res = OrderResult(False, tag_base, [], detail)
            _record(tag_base, {"signal": signal.symbol, "gtt": True}, res)
            return res
        is_paper = paper_trade()
        mode = "PAPER" if is_paper else "LIVE"
        res = OrderResult(
            True,
            tag_base,
            gtt_ids,
            (
                f"{mode} GTT: entry ₹{entry_price:.2f} (exact alert premium) · "
                f"SL ₹{sl_price:.2f} · target ₹{target_price:.2f} · {gtt_summary}"
            ),
            paper=is_paper,
        )
        _record(
            tag_base,
            {
                "instrument_key": inst_key,
                "qty": qty,
                "gtt": True,
                "symbol": signal.symbol,
                "instrument": signal.instrument,
                "entry": entry_price,
                "sl": sl_price,
                "target": target_price,
                "gtt_order_ids": gtt_ids,
            },
            res,
        )
        return res

    order_ids: list[str] = []
    entry_id = _place_regular(
        instrument_key=inst_key,
        transaction_type="BUY",
        order_type="LIMIT",
        quantity=qty,
        product=product,
        price=entry_price,
        tag=f"{tag_base}-entry",
    )
    if not entry_id:
        detail = last_upstox_error() or "Entry order failed"
        res = OrderResult(False, tag_base, [], detail)
        _record(tag_base, {"signal": signal.symbol}, res)
        return res
    order_ids.append(entry_id)

    sl_id = _place_regular(
        instrument_key=inst_key,
        transaction_type="SELL",
        order_type="SL-M",
        quantity=qty,
        product=product,
        trigger_price=sl_price,
        tag=f"{tag_base}-sl",
    )
    if sl_id:
        order_ids.append(sl_id)

    tgt_id = _place_regular(
        instrument_key=inst_key,
        transaction_type="SELL",
        order_type="LIMIT",
        quantity=qty,
        product=product,
        price=target_price,
        tag=f"{tag_base}-tgt",
    )
    if tgt_id:
        order_ids.append(tgt_id)

    is_paper = paper_trade()
    mode = "PAPER" if is_paper else "LIVE"
    res = OrderResult(
        True,
        tag_base,
        order_ids,
        f"{mode} LIMIT entry ₹{entry_price:.2f} (exact alert premium)",
        paper=is_paper,
    )
    _record(tag_base, {"instrument_key": inst_key, "qty": qty}, res)
    return res
