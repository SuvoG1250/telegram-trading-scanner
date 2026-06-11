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
    token_status_line,
)
from upstox_trade_state import get_lots, get_mode, set_lots, set_mode, status_text

logger = logging.getLogger(__name__)

_OFFSET_FILE = __import__("pathlib").Path(__file__).resolve().parent / "data" / "telegram_offset.json"
_poll_lock = threading.Lock()


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


def _reply(chat_id: str, text: str) -> None:
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
    except requests.RequestException:
        logger.exception("Telegram command reply failed")


def _help_text() -> str:
    return (
        "<b>📱 Trading bot commands</b>\n\n"
        "<b>/live</b> — REAL Upstox option orders (Nifty/Sensex CE/PE)\n"
        "<b>/paper</b> — test mode (no broker orders)\n"
        "<b>/stop</b> — disable Upstox orders\n"
        "<b>/status</b> — mode + lots + Upstox token\n\n"
        "<b>Upstox token (daily before 9:15 AM)</b>\n"
        "<b>/upstox_token</b> eyJ… — paste token from app <b>Generate</b> button\n"
        "<b>/upstox_login</b> — OAuth login link (if Generate fails)\n"
        "<b>/upstox_code</b> — paste redirect URL after login\n\n"
        "<b>/lots 1</b> — option lots (1–10)\n"
        "<b>/help</b> — this message\n\n"
        "<i>Use trading app token (NOT Analytics read-only).\n"
        "Token expires ~3:30 AM IST — refresh each morning, then /live.</i>"
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
    cmd = parts[0].lower() if parts else ""
    args = parts[1:]

    if cmd in ("/start", "/help"):
        _reply(chat_id, _help_text())
        return

    if cmd == "/status":
        quotes = "✅ Market data OK" if upstox_configured() and verify_upstox() else "❌ Market data unavailable"
        trade_ok, trade_msg = verify_upstox_trading()
        trade = f"✅ {trade_msg}" if trade_ok else f"❌ {trade_msg}"
        _reply(
            chat_id,
            f"{status_text()}\n{token_status_line()}\n{quotes}\n<b>Orders:</b> {trade}",
        )
        return

    if cmd == "/upstox_token":
        if not args:
            _reply(
                chat_id,
                "Paste token from Upstox app → <b>Generate</b>:\n"
                "<code>/upstox_token eyJhbGciOi...</code>\n\n"
                "<i>One line — copy full token after Generate.</i>",
            )
            return
        token = " ".join(args).strip()
        save_access_token(token, source="telegram")
        trade_ok, trade_msg = verify_upstox_trading()
        if trade_ok:
            _reply(chat_id, f"✅ <b>Upstox token saved</b>\n{token_status_line()}\nSend <b>/live</b> for real orders.")
        else:
            _reply(
                chat_id,
                f"⚠️ Token saved but orders may fail:\n{trade_msg}\n"
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
        set_mode("live")
        _reply(
            chat_id,
            "🔴 <b>LIVE enabled</b> (cloud automation)\n"
            "Nifty/Sensex <b>option</b> signals will place REAL orders on Upstox "
            f"(entry + SL + target, {get_lots()} lot(s)).\n"
            "Send /stop to disable · /status to check.",
        )
        return

    if cmd == "/paper":
        set_mode("paper")
        _reply(chat_id, "📝 <b>PAPER mode</b> — orders logged only, nothing sent to broker.")
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
                params={
                    "offset": offset,
                    "timeout": 0,
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=15,
            )
            data = resp.json()
        except requests.RequestException:
            logger.debug("Telegram poll failed", exc_info=True)
            return 0

        if not data.get("ok"):
            return 0

        handled = 0
        max_id = offset
        for upd in data.get("result", []):
            max_id = max(max_id, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            text = msg.get("text") or ""
            if not chat_id or not text.startswith("/"):
                continue
            if not _allowed_chat(chat_id):
                logger.warning("Ignored command from unauthorized chat %s", chat_id)
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
        "<b>/live</b> — real orders · <b>/paper</b> · <b>/stop</b> · <b>/help</b>",
    )
    mark_automation_session_announced()


def announce_live_runner_start() -> None:
    announce_automation_session()
