"""Telegram control panel — main menu, index picker, positions, GTT cancel."""

from __future__ import annotations

import html
import logging
from typing import Callable

from config import upstox_nifty_lot_size, upstox_sensex_lot_size
from upstox_api import (
    cancel_gtt_order,
    fetch_gtt_orders,
    fetch_short_term_positions,
    last_upstox_error,
    upstox_configured,
)
from upstox_execution_index import INDEX_LABELS, execution_index_label, get_execution_index, set_execution_index
from upstox_execution_strategy import STRATEGY_LABELS, execution_strategy_label, get_execution_strategy
from upstox_trade_state import (
    authorize_live_execution,
    get_lots,
    get_mode,
    pause_live_execution,
    set_execution_strategy_pending,
    set_lots,
    set_mode,
    status_text,
)

logger = logging.getLogger(__name__)

ReplyFn = Callable[..., bool]


def index_picker_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📈 Nifty", "callback_data": "index:nifty"},
                {"text": "📊 Sensex", "callback_data": "index:sensex"},
            ],
            [{"text": "« Back to Menu", "callback_data": "menu:main"}],
        ]
    }


def lots_picker_markup() -> dict:
    rows = []
    row: list[dict] = []
    for n in range(1, 6):
        row.append({"text": str(n), "callback_data": f"lots:{n}"})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "« Back to Menu", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}


def main_menu_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Strategy", "callback_data": "menu:strategy"},
                {"text": "📈 Index", "callback_data": "menu:index"},
            ],
            [
                {"text": "🔢 Lots", "callback_data": "menu:lots"},
                {"text": "📋 Status", "callback_data": "menu:status"},
            ],
            [
                {"text": "📂 Positions", "callback_data": "menu:positions"},
                {"text": "🛑 GTT Orders", "callback_data": "menu:gtt"},
            ],
            [{"text": "🚪 Exit All Positions", "callback_data": "menu:exit_all"}],
            [
                {"text": "🔴 Live", "callback_data": "menu:live"},
                {"text": "📝 Paper", "callback_data": "menu:paper"},
                {"text": "⏹ Stop", "callback_data": "menu:stop"},
            ],
        ]
    }


def gtt_cancel_markup(gtt_ids: list[str]) -> dict:
    rows = []
    for gid in gtt_ids[:8]:
        short = gid if len(gid) <= 18 else f"{gid[:8]}…{gid[-6:]}"
        rows.append([{"text": f"Cancel {short}", "callback_data": f"gtt:cancel:{gid}"}])
    rows.append([{"text": "« Back to Menu", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}


def main_menu_text() -> str:
    return (
        "<b>🎛 Trading Control Panel</b>\n\n"
        f"{status_text()}\n\n"
        "Use buttons below to configure strategy, index, lots, "
        "view positions, or manage GTT orders."
    )


def index_picker_text() -> str:
    return (
        "<b>Select Index for Trade</b>\n\n"
        "Choose which options chain Upstox should trade today:\n"
        "• <b>Nifty</b> — GTT SL 15 pts · Target 30 pts\n"
        "• <b>Sensex</b> — GTT SL 20 pts · Target 50 pts\n\n"
        f"Current: <b>{execution_index_label()}</b>"
    )


def strategy_picker_text(*, mode_hint: str = "live") -> str:
    return (
        "<b>Select today's auto-execution strategy</b>\n\n"
        "Both strategies still send <b>Telegram alerts</b>.\n"
        "Only the selected strategy + index will place <b>Upstox GTT orders</b> "
        "at exact alert premium.\n\n"
        f"• <b>{STRATEGY_LABELS['st_tsl']}</b>\n"
        f"• <b>{STRATEGY_LABELS['ema_macd_sync']}</b>\n\n"
        f"<i>After strategy, pick index (Nifty/Sensex). Mode: {mode_hint.upper()}</i>"
    )


def _today_gtt_ids_from_log() -> list[dict]:
    from upstox_orders import list_today_gtt_orders

    return list_today_gtt_orders()


def format_positions_text() -> str:
    lines = ["<b>📂 Open Positions</b>\n"]

    from position_lifecycle import load_active_positions

    blob = load_active_positions()
    premium_open = [r for r in blob.get("premium", []) if r.get("status") == "OPEN"]
    if premium_open:
        lines.append("<b>Tracked premium plans:</b>")
        for row in premium_open:
            sym = html.escape(str(row.get("symbol") or "—"))
            entry = float(row.get("entry") or 0)
            sl = float(row.get("stop_loss") or 0)
            tp = float(row.get("target") or 0)
            lines.append(
                f"• {sym} · Entry ₹{entry:.2f} · SL ₹{sl:.2f} · Tgt ₹{tp:.2f}"
            )
    else:
        lines.append("<i>No open tracked premium positions.</i>")

    if upstox_configured():
        broker = fetch_short_term_positions()
        opts = [
            p
            for p in broker
            if p.get("quantity") and int(p.get("quantity") or 0) != 0
        ]
        if opts:
            lines.append("\n<b>Upstox broker positions:</b>")
            for p in opts[:10]:
                sym = html.escape(str(p.get("trading_symbol") or p.get("instrument_token") or "—"))
                qty = p.get("quantity")
                pnl = p.get("pnl")
                avg = p.get("average_price") or p.get("buy_avg")
                pnl_s = f" · P&L ₹{float(pnl):,.2f}" if pnl is not None else ""
                lines.append(f"• {sym} · Qty {qty} · Avg ₹{avg}{pnl_s}")
        elif not premium_open:
            lines.append("\n<i>No open Upstox positions.</i>")

    return "\n".join(lines)


def format_gtt_text() -> str:
    lines = ["<b>🛑 Active GTT Orders</b>\n"]
    logged = _today_gtt_ids_from_log()
    if logged:
        lines.append("<b>Today's bot GTT placements:</b>")
        for row in logged:
            sym = html.escape(str(row.get("symbol") or row.get("tag") or "—"))
            entry = row.get("entry")
            sl = row.get("sl")
            tgt = row.get("target")
            ids = row.get("gtt_order_ids") or row.get("order_ids") or []
            id_s = ", ".join(ids) if ids else "—"
            lvl = ""
            if entry is not None:
                lvl = f" · Entry ₹{float(entry):.2f} · SL ₹{float(sl):.2f} · Tgt ₹{float(tgt):.2f}"
            lines.append(f"• {sym}{lvl}\n  <code>{html.escape(id_s)}</code>")
    else:
        lines.append("<i>No GTT orders logged today.</i>")

    if upstox_configured():
        live = fetch_gtt_orders()
        if live:
            lines.append(f"\n<b>Upstox API ({len(live)} GTT):</b>")
            for g in live[:8]:
                gid = str(g.get("gtt_order_id") or "—")
                sym = html.escape(str(g.get("trading_symbol") or g.get("instrument_token") or "—"))
                rules = g.get("rules") or []
                parts = []
                for rule in rules:
                    if isinstance(rule, dict):
                        st = rule.get("strategy")
                        px = rule.get("trigger_price")
                        rs = rule.get("status")
                        if st and px is not None:
                            parts.append(f"{st} ₹{px} ({rs})")
                lines.append(f"• {sym} · <code>{html.escape(gid)}</code>")
                if parts:
                    lines.append(f"  {' · '.join(parts)}")
        elif not logged:
            lines.append("\n<i>No active GTT on Upstox.</i>")

    lines.append("\nTap a cancel button below to cancel a logged GTT.")
    return "\n".join(lines)


def handle_menu_callback(chat_id: str, data: str, reply: ReplyFn) -> bool:
    """Handle menu:* callbacks. Returns True if handled."""
    from daily_strategy_setup import daily_strategy_markup

    action = data.split(":", 1)[1] if ":" in data else ""

    if action == "main":
        reply(chat_id, main_menu_text(), reply_markup=main_menu_markup())
        return True

    if action == "strategy":
        reply(
            chat_id,
            strategy_picker_text(mode_hint=get_mode() if get_mode() in ("live", "paper") else "live"),
            reply_markup=daily_strategy_markup(),
        )
        return True

    if action == "index":
        reply(chat_id, index_picker_text(), reply_markup=index_picker_markup())
        return True

    if action == "lots":
        reply(
            chat_id,
            f"<b>Select lot size</b> (current: <b>{get_lots()}</b>)\n"
            f"Nifty lot={upstox_nifty_lot_size()} · Sensex lot={upstox_sensex_lot_size()}",
            reply_markup=lots_picker_markup(),
        )
        return True

    if action == "status":
        from upstox_api import verify_upstox, verify_upstox_trading
        from upstox_token import token_kind_label, token_status_line

        quotes = "✅ Market data OK" if upstox_configured() and verify_upstox() else "❌ Market data unavailable"
        trade_ok, trade_msg = verify_upstox_trading()
        trade = f"✅ {trade_msg}" if trade_ok else f"❌ {trade_msg}"
        kind = token_kind_label() if upstox_configured() else "missing"
        reply(
            chat_id,
            f"{status_text()}\n{token_status_line()}\n<b>Token:</b> {kind}\n{quotes}\n<b>Orders:</b> {trade}",
            reply_markup=main_menu_markup(),
        )
        return True

    if action == "positions":
        reply(chat_id, format_positions_text(), reply_markup=main_menu_markup())
        return True

    if action == "gtt":
        logged = _today_gtt_ids_from_log()
        ids: list[str] = []
        for row in logged:
            for gid in row.get("gtt_order_ids") or row.get("order_ids") or []:
                if gid and not str(gid).startswith("PAPER"):
                    ids.append(str(gid))
        markup = gtt_cancel_markup(ids) if ids else main_menu_markup()
        reply(chat_id, format_gtt_text(), reply_markup=markup)
        return True

    if action == "live":
        from upstox_token import token_is_expired

        if not upstox_configured() or token_is_expired():
            reply(chat_id, "❌ Upstox token missing or expired. Send <code>/upstox_token</code> first.")
            return True
        strategy = get_execution_strategy()
        idx = get_execution_index()
        if not strategy:
            from daily_strategy_setup import daily_strategy_markup

            reply(
                chat_id,
                strategy_picker_text(mode_hint="live"),
                reply_markup=daily_strategy_markup(),
            )
            return True
        if not idx:
            reply(chat_id, index_picker_text(), reply_markup=index_picker_markup())
            return True
        set_mode("live")
        reply(
            chat_id,
            f"🔴 <b>LIVE enabled</b>\n{status_text()}",
            reply_markup=main_menu_markup(),
        )
        return True

    if action == "paper":
        from upstox_execution_strategy import get_execution_strategy

        if not get_execution_strategy() or not get_execution_index():
            reply(
                chat_id,
                "Pick <b>Strategy</b> and <b>Index</b> first.",
                reply_markup=main_menu_markup(),
            )
            return True
        set_mode("paper")
        reply(
            chat_id,
            f"📝 <b>PAPER mode</b>\n<b>Strategy:</b> {execution_strategy_label()}\n"
            f"<b>Index:</b> {execution_index_label()}",
            reply_markup=main_menu_markup(),
        )
        return True

    if action == "exit_all":
        from upstox_api import place_order

        if not upstox_configured():
            reply(chat_id, "❌ Upstox not configured.")
            return True
        positions = fetch_short_term_positions()
        closed = 0
        errors: list[str] = []
        for p in positions:
            try:
                qty = int(p.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty == 0:
                continue
            inst = str(p.get("instrument_token") or p.get("instrument_key") or "")
            product = str(p.get("product") or "I")
            if not inst:
                continue
            side = "SELL" if qty > 0 else "BUY"
            payload = {
                "quantity": abs(qty),
                "product": product,
                "validity": "DAY",
                "price": 0,
                "tag": "tg-exit-all"[:20],
                "instrument_token": inst,
                "order_type": "MARKET",
                "transaction_type": side,
                "disclosed_quantity": 0,
                "trigger_price": 0,
                "is_amo": False,
                "slice": True,
                "market_protection": -1,
            }
            ids, _ = place_order(payload)
            if ids:
                closed += 1
            else:
                sym = str(p.get("trading_symbol") or inst)
                errors.append(sym)
        msg = f"🚪 <b>Exit requests sent</b> for {closed} position(s)."
        if errors:
            msg += f"\n❌ Failed: {', '.join(html.escape(x) for x in errors[:5])}"
        reply(chat_id, msg, reply_markup=main_menu_markup())
        return True

    if action == "stop":
        set_mode("off")
        reply(chat_id, "⏹ <b>Orders OFF</b> — alerts continue.", reply_markup=main_menu_markup())
        return True

    return False


def handle_index_callback(chat_id: str, index_key: str, reply: ReplyFn, *, pending_mode: str = "live") -> None:
    from daily_strategy_setup import confirmation_message

    if index_key not in INDEX_LABELS:
        reply(chat_id, "❌ Unknown index.")
        return

    strategy = get_execution_strategy()
    if not strategy:
        set_execution_index(index_key)  # type: ignore[arg-type]
        reply(
            chat_id,
            f"✅ Index set to <b>{INDEX_LABELS[index_key]}</b>.\n"
            "Now pick a strategy from the menu.",
            reply_markup=main_menu_markup(),
        )
        return

    set_execution_index(index_key)  # type: ignore[arg-type]
    mode = pending_mode if pending_mode in ("live", "paper") else "live"
    if mode == "live":
        authorize_live_execution(strategy, index=index_key)
    else:
        set_mode("paper")

    extra = status_text()
    reply(
        chat_id,
        confirmation_message(strategy, index_key=index_key) + "\n\n" + extra,
        reply_markup=main_menu_markup(),
    )


def handle_exec_callback(chat_id: str, strategy_key: str, reply: ReplyFn) -> None:
    from daily_strategy_setup import confirmation_message

    if strategy_key == "pause":
        pause_live_execution(clear_strategy=True)
        reply(chat_id, confirmation_message("pause"), reply_markup=main_menu_markup())
        return

    if strategy_key not in STRATEGY_LABELS:
        reply(chat_id, "❌ Unknown strategy.")
        return

    set_execution_strategy_pending(strategy_key)
    reply(
        chat_id,
        f"✅ Strategy: <b>{STRATEGY_LABELS[strategy_key]}</b>\n\n{index_picker_text()}",
        reply_markup=index_picker_markup(),
    )


def handle_lots_callback(chat_id: str, n: int, reply: ReplyFn) -> None:
    set_lots(n)
    reply(
        chat_id,
        f"✅ Lots set to <b>{get_lots()}</b>",
        reply_markup=main_menu_markup(),
    )


def handle_gtt_cancel_callback(chat_id: str, gtt_id: str, reply: ReplyFn) -> None:
    if not upstox_configured():
        reply(chat_id, "❌ Upstox not configured.")
        return
    if gtt_id.startswith("PAPER"):
        reply(chat_id, "📝 Paper GTT — nothing to cancel on broker.")
        return
    ok = cancel_gtt_order(gtt_id)
    if ok:
        reply(chat_id, f"✅ GTT cancelled: <code>{html.escape(gtt_id)}</code>", reply_markup=main_menu_markup())
    else:
        detail = last_upstox_error() or "Cancel failed"
        reply(chat_id, f"❌ {html.escape(detail)}", reply_markup=main_menu_markup())
