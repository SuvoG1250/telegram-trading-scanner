"""Bridge Telegram signals → Upstox WebSocket + auto orders (options only)."""

from __future__ import annotations

import logging

from telegram_client import Signal, send_plain
from upstox_orders import OrderResult, execute_signal_orders
from upstox_trade_state import auto_trade_enabled

logger = logging.getLogger(__name__)

UPSTOX_OPTION_INSTRUMENTS = frozenset({"NIFTY_OPTION", "SENSEX_OPTION"})


def is_upstox_option_signal(signal: Signal) -> bool:
    return signal.instrument in UPSTOX_OPTION_INSTRUMENTS


def maybe_execute_upstox_trade(signal: Signal) -> OrderResult | None:
    if not auto_trade_enabled():
        return None
    if not is_upstox_option_signal(signal):
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
