"""Telegram bot commands — control Upstox live / paper / stop."""

from __future__ import annotations

import json
import logging
import threading

import requests

from config import TELEGRAM_COMMANDS_ENABLED, TELEGRAM_TOKEN, telegram_chat_ids
from telegram_client import send_plain
from upstox_api import upstox_configured, verify_upstox
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
                "disable_web_page_preview": True,
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
        "<b>/status</b> — current mode + lots\n"
        "<b>/lots 1</b> — set option lots (1–10)\n"
        "<b>/help</b> — this message\n\n"
        "<i>Stocks &amp; BTST = Telegram alerts only.\n"
        "Cloud automation runs 9:10 AM–3:30 PM IST — send /live when you want real orders.</i>"
    )


def _handle_command(chat_id: str, text: str) -> None:
    cmd = (text or "").strip().split()[0].lower()
    args = (text or "").strip().split()[1:]

    if cmd in ("/start", "/help"):
        _reply(chat_id, _help_text())
        return

    if cmd == "/status":
        up = "✅ Upstox token OK" if upstox_configured() and verify_upstox() else "❌ Upstox not connected"
        _reply(chat_id, f"{status_text()}\n{up}")
        return

    if cmd == "/live":
        if not upstox_configured():
            _reply(chat_id, "❌ Set <b>UPSTOX_ACCESS_TOKEN</b> first (Apps → Analytics → Generate).")
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
    send_plain(
        "<b>🤖 Automation session online</b> (GitHub + cron)\n\n"
        f"{status_text()}\n\n"
        "Send <b>/live</b> → real Upstox option orders\n"
        "<b>/paper</b> → test · <b>/stop</b> → off · <b>/help</b> → commands",
    )
    mark_automation_session_announced()


def announce_live_runner_start() -> None:
    announce_automation_session()
