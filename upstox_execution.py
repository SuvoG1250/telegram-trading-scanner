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

    from upstox_execution_strategy import (
        execution_strategy_label,
        get_execution_strategy,
        signal_allowed_for_execution,
    )

    if not signal_allowed_for_execution(signal.strategy):
        selected = get_execution_strategy()
        if selected:
            logger.info(
                "Upstox skip %s — today's auto-exec is %s only (alert still sent).",
                signal.strategy,
                execution_strategy_label(),
            )
        else:
            logger.info("Upstox skip %s — no execution strategy selected (/live).", signal.strategy)
        return None

    from upstox_api import upstox_configured, verify_upstox_trading
    from upstox_token import token_is_likely_analytics
    from upstox_trade_state import paper_trade

    if not upstox_configured():
        return None
    if not paper_trade():
        if token_is_likely_analytics():
            send_plain(
                "❌ <b>Upstox order skipped</b> — Analytics token is read-only.\n"
                "Use app <b>Generate</b> token → <code>/upstox_token</code> → <b>/live</b>"
            )
            return OrderResult(
                False,
                "analytics-token",
                [],
                "Analytics read-only token",
            )
        trade_ok, trade_msg = verify_upstox_trading()
        if not trade_ok:
            send_plain(f"❌ <b>Upstox order skipped</b>\n{trade_msg}")
            return OrderResult(False, "trading-token", [], trade_msg or "Trading token invalid")
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
