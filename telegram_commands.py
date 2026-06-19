"""Telegram bot commands — control Upstox live / paper / stop / daily token refresh."""

from __future__ import annotations

import json
import logging
import threading

import requests

from config import TELEGRAM_COMMANDS_ENABLED, TELEGRAM_TOKEN, telegram_chat_ids
from telegram_client import send_plain
from upstox_api import upstox_configured, verify_upstox, verify_upstox_trading
from upstox_token import (
    build_auth_url,
    exchange_auth_code,
    save_access_token,
    token_is_expired,
    token_is_likely_analytics,
    token_kind_label,
    token_status_line,
)
from upstox_trade_state import get_lots, get_mode, set_lots, set_mode, status_text

logger = logging.getLogger(__name__)

_OFFSET_FILE = __import__("pathlib").Path(__file__).resolve().parent / "data" / "telegram_offset.json"
_poll_lock = threading.Lock()
_denied_chat_warned: set[str] = set()


def _allowed_chat(chat_id: str) -> bool:
    allowed = {str(c) for c in telegram_chat_ids()}
    return str(chat_id) in allowed


def _load_offset() -> int:
    try:
        if _OFFSET_FILE.exists():
            return int(json.loads(_OFFSET_FILE.read_text(encoding="utf-8")).get("offset", 0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return 0


def _save_offset(offset: int) -> None:
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def _normalize_command(text: str) -> str:
    """Strip @BotName suffix Telegram adds in groups."""
    parts = (text or "").strip().split()
    if not parts:
        return ""
    head = parts[0].lower()
    if "@" in head:
        head = head.split("@", 1)[0]
    return head


def _reply(chat_id: str, text: str, *, reply_markup: dict | None = None) -> bool:
    if not TELEGRAM_TOKEN:
        return False
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
            timeout=20,
        )
        body = resp.json() if resp.content else {}
        if not resp.ok or not body.get("ok"):
            logger.error(
                "Telegram reply failed chat=%s status=%s body=%s",
                chat_id,
                resp.status_code,
                body,
            )
            return False
        return True
    except requests.RequestException:
        logger.exception("Telegram command reply failed")
        return False


def _answer_callback(callback_id: str, text: str = "") -> None:
    if not TELEGRAM_TOKEN or not callback_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text[:180]},
            timeout=15,
        )
    except requests.RequestException:
        logger.debug("answerCallbackQuery failed", exc_info=True)


def _strategy_picker_markup() -> dict:
    from daily_strategy_setup import daily_strategy_markup

    return daily_strategy_markup()


def _send_strategy_picker(chat_id: str, *, mode_hint: str = "live") -> None:
    from upstox_execution_strategy import STRATEGY_LABELS

    _reply(
        chat_id,
        "<b>Select today's auto-execution strategy</b>\n\n"
        "Both strategies still send <b>Telegram alerts</b> every day.\n"
        "Only the strategy you pick here will place <b>Upstox orders</b> "
        "at the <b>exact alert premium</b> (zero buffer).\n\n"
        f"• <b>{STRATEGY_LABELS['st_tsl']}</b>\n"
        f"• <b>{STRATEGY_LABELS['ema_macd_sync']}</b>\n\n"
        f"<i>Mode after selection: {mode_hint.upper()}</i>",
        reply_markup=_strategy_picker_markup(),
    )


def _activate_live_mode(chat_id: str) -> None:
    from upstox_execution_strategy import execution_strategy_label, get_execution_strategy

    if not get_execution_strategy():
        _send_strategy_picker(chat_id, mode_hint="live")
        return
    set_mode("live")
    _reply(
        chat_id,
        "🔴 <b>LIVE enabled</b>\n"
        f"<b>Auto-exec:</b> {execution_strategy_label()}\n"
        f"<b>Orders:</b> GTT/LIMIT · entry = alert premium exactly · {get_lots()} lot(s)\n"
        "Both strategies still alert — only selected one trades.\n"
        "Send /strategy to change · /stop to disable.",
    )


def _handle_callback(chat_id: str, callback_id: str, data: str) -> None:
    if not data.startswith("exec:"):
        _answer_callback(callback_id)
        return

    from daily_strategy_setup import confirmation_message
    from upstox_execution_strategy import STRATEGY_LABELS
    from upstox_trade_state import authorize_live_execution, pause_live_execution

    key = data.split(":", 1)[1].strip().lower()

    if key == "pause":
        pause_live_execution(clear_strategy=True)
        _answer_callback(callback_id, "Paused for today")
        _reply(chat_id, confirmation_message("pause"))
        return

    if key not in STRATEGY_LABELS:
        _answer_callback(callback_id, "Unknown choice")
        return

    authorize_live_execution(key)
    _PENDING_MODE.pop(chat_id, None)
    label = STRATEGY_LABELS[key]
    _answer_callback(callback_id, f"Selected: {label}")
    _reply(
        chat_id,
        confirmation_message(key)
        + "\n\n"
        + status_text(),
    )


_PENDING_MODE: dict[str, str] = {}


def _help_text() -> str:
    return (
        "<b>📱 Trading bot commands</b>\n\n"
        "<b>/live</b> — enable REAL Upstox orders (pick strategy first)\n"
        "<b>/paper</b> — test mode (pick strategy, no real broker orders)\n"
        "<b>/strategy</b> — change today's auto-exec strategy\n"
        "<b>/stop</b> — disable Upstox orders\n"
        "<b>/status</b> — mode + strategy + token\n\n"
        "<b>Daily setup</b> — ~8:30 AM IST buttons: Original / EMA+MACD / Pause\n"
        "Execution stays paused until you tap a button.\n\n"
        "<b>Upstox token (daily before 9:15 AM)</b>\n"
        "<b>/upstox_token</b> eyJ… — paste token from app <b>Generate</b> button\n"
        "<b>/upstox_login</b> — OAuth login link (if Generate fails)\n"
        "<b>/upstox_code</b> — paste redirect URL after login\n\n"
        "<b>/lots 1</b> — option lots (1–10)\n"
        "<b>/help</b> — this message\n\n"
        "<i>Use trading app token (NOT Analytics read-only).\n"
        "Token expires ~3:30 AM IST — refresh each morning, then tap strategy button.</i>"
    )


def _upstox_connect_help() -> str:
    return (
        "❌ <b>Upstox trading token required</b>\n\n"
        "1) Upstox Developer Apps → your <b>Telegram Bot</b> app\n"
        "2) Click purple <b>Generate</b> (Access Token — not Analytics tab)\n"
        "3) Send: <code>/upstox_token paste_token_here</code>\n"
        "4) Send <b>/live</b>\n\n"
        "Or use <code>/upstox_login</code> for browser OAuth."
    )


def _handle_command(chat_id: str, text: str) -> None:
    parts = (text or "").strip().split()
    cmd = _normalize_command(text)
    args = parts[1:] if parts else []

    if cmd in ("/start", "/help"):
        _reply(chat_id, _help_text())
        return

    if cmd == "/status":
        quotes = "✅ Market data OK" if upstox_configured() and verify_upstox() else "❌ Market data unavailable"
        trade_ok, trade_msg = verify_upstox_trading()
        trade = f"✅ {trade_msg}" if trade_ok else f"❌ {trade_msg}"
        kind = token_kind_label() if upstox_configured() else "missing"
        _reply(
            chat_id,
            f"{status_text()}\n{token_status_line()}\n<b>Token type:</b> {kind}\n{quotes}\n<b>Orders:</b> {trade}",
        )
        return

    if cmd == "/upstox_token":
        if not args:
            _reply(
                chat_id,
                "Paste token from Upstox app → <b>Generate</b> (main app page):\n"
                "<code>/upstox_token eyJhbGciOi...</code>\n\n"
                "<i>NOT Analytics tab — that token is read-only (no orders).</i>",
            )
            return
        token = " ".join(args).strip()
        save_access_token(token, source="telegram")
        trade_ok, trade_msg = verify_upstox_trading()
        kind = token_kind_label(token)
        if trade_ok:
            _reply(chat_id, f"✅ <b>Trading token saved</b>\n{token_status_line()}\nSend <b>/live</b> for real orders.")
        elif token_is_likely_analytics(token):
            _reply(
                chat_id,
                "⚠️ <b>Analytics token saved</b> (read-only)\n"
                f"{token_status_line()}\n\n"
                "✅ Option <b>premiums/quotes</b> will work\n"
                "❌ <b>Orders will NOT work</b> with this token\n\n"
                "For live orders:\n"
                "1) Upstox Apps → <b>your bot app</b> (not Analytics)\n"
                "2) Click purple <b>Generate</b>\n"
                "3) Send <code>/upstox_token</code> with that token\n"
                "4) Send <b>/live</b>",
            )
        else:
            _reply(
                chat_id,
                f"⚠️ Token saved ({kind}) but orders may fail:\n{trade_msg}\n"
                "Use app <b>Generate</b> (not Analytics tab).",
            )
        return

    if cmd == "/upstox_login":
        url, err = build_auth_url()
        if not url:
            _reply(chat_id, f"❌ {err}\nSet UPSTOX_REDIRECT_URI to match your app Redirect URL.")
            return
        _reply(
            chat_id,
            "<b>Upstox login</b>\n"
            f"1) Open: <a href=\"{url}\">Authorize Upstox</a>\n"
            "2) Log in and approve\n"
            "3) Copy full redirect URL from browser\n"
            "4) Send: <code>/upstox_code paste_url_here</code>",
        )
        return

    if cmd == "/upstox_code":
        if not args:
            _reply(chat_id, "Usage: <code>/upstox_code https://your-redirect?code=...</code>")
            return
        raw = " ".join(args).strip()
        _token, err = exchange_auth_code(raw)
        if not _token:
            _reply(chat_id, f"❌ Token exchange failed: {err}")
            return
        trade_ok, trade_msg = verify_upstox_trading()
        emoji = "✅" if trade_ok else "⚠️"
        _reply(chat_id, f"{emoji} <b>Upstox OAuth OK</b>\n{token_status_line()}\n{trade_msg}\nSend <b>/live</b>.")
        return

    if cmd == "/live":
        if not upstox_configured():
            _reply(chat_id, _upstox_connect_help())
            return
        if token_is_expired():
            _reply(chat_id, f"❌ Token expired.\n{token_status_line()}\nRefresh with <code>/upstox_token</code>.")
            return
        trade_ok, trade_msg = verify_upstox_trading()
        if not trade_ok:
            _reply(chat_id, f"❌ {trade_msg}\n\n{_upstox_connect_help()}")
            return
        _PENDING_MODE[chat_id] = "live"
        _activate_live_mode(chat_id)
        return

    if cmd == "/paper":
        _PENDING_MODE[chat_id] = "paper"
        from upstox_execution_strategy import get_execution_strategy

        if not get_execution_strategy():
            _send_strategy_picker(chat_id, mode_hint="paper")
            return
        set_mode("paper")
        from upstox_execution_strategy import execution_strategy_label

        _reply(
            chat_id,
            f"📝 <b>PAPER mode</b> — GTT flow simulated only.\n"
            f"<b>Auto-exec:</b> {execution_strategy_label()}",
        )
        return

    if cmd == "/strategy":
        _PENDING_MODE[chat_id] = get_mode() if get_mode() in ("live", "paper") else "live"
        _send_strategy_picker(chat_id, mode_hint=_PENDING_MODE.get(chat_id, "live"))
        return

    if cmd == "/stop":
        set_mode("off")
        _reply(chat_id, "⏹ <b>Upstox orders OFF</b> — Telegram alerts continue.")
        return

    if cmd == "/lots":
        if not args:
            _reply(chat_id, f"Current lots: <b>{get_lots()}</b>. Usage: <code>/lots 1</code>")
            return
        try:
            n = int(args[0])
        except ValueError:
            _reply(chat_id, "Usage: <code>/lots 1</code> (1–10)")
            return
        set_lots(n)
        _reply(chat_id, f"✅ Lots set to <b>{get_lots()}</b>")
        return


def poll_telegram_commands() -> int:
    """Process pending Telegram commands. Returns count handled."""
    if not TELEGRAM_COMMANDS_ENABLED or not TELEGRAM_TOKEN:
        return 0

    with _poll_lock:
        offset = _load_offset()
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 0},
                timeout=20,
            )
            data = resp.json()
        except requests.RequestException:
            logger.debug("Telegram poll failed", exc_info=True)
            return 0

        if not data.get("ok"):
            logger.warning("Telegram getUpdates error: %s", data.get("description", data))
            return 0

        handled = 0
        max_id = offset
        allowed = {str(c) for c in telegram_chat_ids()}
        for upd in data.get("result", []):
            max_id = max(max_id, int(upd.get("update_id", 0)) + 1)

            cb = upd.get("callback_query") or {}
            if cb:
                chat = cb.get("message", {}).get("chat") or {}
                chat_id = str(chat.get("id", ""))
                cb_id = str(cb.get("id", ""))
                cb_data = str(cb.get("data") or "")
                if chat_id and _allowed_chat(chat_id):
                    try:
                        _handle_callback(chat_id, cb_id, cb_data)
                        handled += 1
                    except Exception:
                        logger.exception("Callback failed: %s", cb_data)
                continue

            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            text = (msg.get("text") or "").strip()
            if not chat_id or not text.startswith("/"):
                continue
            if not _allowed_chat(chat_id):
                logger.warning(
                    "Ignored command from chat %s (allowed: %s)",
                    chat_id,
                    ", ".join(sorted(allowed)) if allowed else "(none configured)",
                )
                if chat_id not in _denied_chat_warned:
                    _denied_chat_warned.add(chat_id)
                    _reply(
                        chat_id,
                        "⛔ <b>Unauthorized chat</b>\n"
                        f"Your chat id: <code>{chat_id}</code>\n"
                        "Add this to GitHub secret <code>TELEGRAM_GROUP_CHAT_ID</code> "
                        "or <code>TELEGRAM_CHAT_ID</code>, then retry.",
                    )
                continue
            try:
                _handle_command(chat_id, text)
                handled += 1
            except Exception:
                logger.exception("Command failed: %s", text)

        if max_id > offset:
            _save_offset(max_id)
        return handled


def start_command_poller(interval_sec: float = 2.0) -> threading.Thread:
    """Background thread for Telegram commands (live runner)."""

    def _loop() -> None:
        while True:
            try:
                poll_telegram_commands()
            except Exception:
                logger.exception("Command poller error")
            threading.Event().wait(interval_sec)

    t = threading.Thread(target=_loop, name="telegram-commands", daemon=True)
    t.start()
    return t


def announce_automation_session() -> None:
    """Once per day when GitHub/cron session starts."""
    from market_time import is_weekday
    from state import automation_session_announced, mark_automation_session_announced

    if not is_weekday() or automation_session_announced():
        return

    token_note = token_status_line()
    if token_is_expired() or "No Upstox token" in token_note:
        token_note += "\n<b>Action:</b> <code>/upstox_token</code> then <code>/live</code>"

    send_plain(
        "<b>🤖 Automation session online</b> (GitHub + cron)\n\n"
        f"{status_text()}\n{token_note}\n\n"
        "<b>/upstox_token</b> — refresh trading token (daily)\n"
        "<b>/live</b> — pick strategy + real GTT orders · <b>/strategy</b> to switch\n"
        "<b>/paper</b> · <b>/stop</b> · <b>/help</b>",
    )
    mark_automation_session_announced()


def announce_live_runner_start() -> None:
    announce_automation_session()
