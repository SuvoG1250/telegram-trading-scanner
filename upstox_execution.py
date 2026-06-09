"""Bridge Telegram signals → Upstox WebSocket + auto orders."""

from __future__ import annotations

import logging

from config import UPSTOX_AUTO_TRADE_ENABLED
from telegram_client import Signal, send_plain
from upstox_orders import OrderResult, execute_signal_orders

logger = logging.getLogger(__name__)


def maybe_execute_upstox_trade(signal: Signal) -> OrderResult | None:
    if not UPSTOX_AUTO_TRADE_ENABLED:
        return None
    try:
        result = execute_signal_orders(signal)
    except Exception:
        logger.exception("Upstox auto-trade failed for %s", signal.symbol)
        return None
    if result is None:
        return None
    _notify_order_result(signal, result)
    return result


def _notify_order_result(signal: Signal, result: OrderResult) -> None:
    emoji = "📝" if result.paper else ("✅" if result.ok else "❌")
    ids = ", ".join(result.order_ids) if result.order_ids else "—"
    text = (
        f"{emoji} <b>Upstox {'Paper' if result.paper else 'Live'}</b> — {signal.symbol}\n"
        f"{result.message}\n"
        f"<i>Order IDs: {ids}</i>"
    )
    send_plain(text)
