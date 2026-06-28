"""Telegram bot commands — control Upstox live / paper / stop / daily token refresh."""

from __future__ import annotations

import json
import html
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager

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
_POLL_LOCK_FILE = _OFFSET_FILE.parent / "telegram_poll.lock"
_poll_lock = threading.Lock()
_denied_chat_warned: set[str] = set()
_poller_started = False
_poll_owner_handle = None
_conflict_backoff_until = 0.0
_last_conflict_log = 0.0


def acquire_telegram_poll_ownership() -> bool:
    """
    Exclusive getUpdates ownership for the lifetime of this process.
    Call once from telegram_command_listener.py only.
    """
    global _poll_owner_handle
    if _poll_owner_handle is not None:
        return True
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        _poll_owner_handle = open(_POLL_LOCK_FILE, "a+", encoding="utf-8")
        _poll_owner_handle.write(str(os.getpid()))
        _poll_owner_handle.flush()
        logger.info("Telegram poll ownership acquired (pid=%s, win32)", os.getpid())
        return True

    import fcntl

    handle = open(_POLL_LOCK_FILE, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        logger.error(
            "Another Telegram poller already holds the lock (%s). Exiting.",
            _POLL_LOCK_FILE,
        )
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _poll_owner_handle = handle
    logger.info("Telegram poll ownership acquired (pid=%s)", os.getpid())
    return True


def release_telegram_poll_ownership() -> None:
    global _poll_owner_handle
    if _poll_owner_handle is None:
        return
    if sys.platform != "win32":
        import fcntl

        try:
            fcntl.flock(_poll_owner_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
    try:
        _poll_owner_handle.close()
    except OSError:
        pass
    _poll_owner_handle = None


@contextmanager
def _cross_process_poll_lock():
    """Only one process may call getUpdates (Telegram allows one consumer)."""
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        with _poll_lock:
            yield
        return

    import fcntl

    lock_handle = open(_POLL_LOCK_FILE, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        raise
    try:
        yield
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def _callback_chat_id(cb: dict) -> str:
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("id") is not None:
        return str(chat["id"])
    user = cb.get("from") or {}
    if user.get("id") is not None:
        return str(user["id"])
    return ""


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
    from telegram_client import _split_telegram_text

    chunks = _split_telegram_text(text)
    ok = False
    for i, chunk in enumerate(chunks):
        payload: dict = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        if reply_markup and i == 0:
            payload["reply_markup"] = reply_markup
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json=payload,
                timeout=30,
            )
            body = resp.json() if resp.content else {}
            if resp.ok and body.get("ok"):
                ok = True
            else:
                logger.error(
                    "Telegram reply failed chat=%s part=%s/%s status=%s body=%s",
                    chat_id,
                    i + 1,
                    len(chunks),
                    resp.status_code,
                    body,
                )
        except requests.RequestException:
            logger.exception("Telegram command reply failed part %s/%s", i + 1, len(chunks))
    return ok


def _answer_callback(callback_id: str, text: str = "", *, show_alert: bool = False) -> None:
    if not TELEGRAM_TOKEN or not callback_id:
        return
    payload: dict = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:180]
        payload["show_alert"] = show_alert
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
            json=payload,
            timeout=15,
        )
        body = resp.json() if resp.content else {}
        if not resp.ok or not body.get("ok"):
            logger.warning("answerCallbackQuery failed: %s", body)
    except requests.RequestException:
        logger.warning("answerCallbackQuery request failed", exc_info=True)


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
    from upstox_execution_index import get_execution_index
    from upstox_execution_strategy import execution_strategy_label, get_execution_strategy
    from telegram_control_panel import index_picker_markup, index_picker_text, main_menu_markup

    if not get_execution_strategy():
        _send_strategy_picker(chat_id, mode_hint="live")
        return
    if not get_execution_index():
        _reply(
            chat_id,
            index_picker_text(),
            reply_markup=index_picker_markup(),
        )
        return
    set_mode("live")
    _reply(
        chat_id,
        "🔴 <b>LIVE enabled</b>\n"
        f"<b>Auto-exec:</b> {execution_strategy_label()}\n"
        f"<b>Orders:</b> GTT at exact alert premium · {get_lots()} lot(s)\n"
        "Send /menu for control panel · /stop to disable.",
        reply_markup=main_menu_markup(),
    )


def _handle_callback(chat_id: str, callback_id: str, data: str) -> None:
    from telegram_control_panel import (
        handle_exec_callback,
        handle_gtt_cancel_callback,
        handle_index_callback,
        handle_lots_callback,
        handle_menu_callback,
    )

    if data.startswith("menu:"):
        handle_menu_callback(chat_id, data, _reply)
        return

    if data.startswith("index:"):
        key = data.split(":", 1)[1].strip().lower()
        handle_index_callback(chat_id, key, _reply, pending_mode=_PENDING_MODE.get(chat_id, "live"))
        _PENDING_MODE.pop(chat_id, None)
        return

    if data.startswith("lots:"):
        try:
            n = int(data.split(":", 1)[1])
        except ValueError:
            _reply(chat_id, "Invalid lots.")
            return
        handle_lots_callback(chat_id, n, _reply)
        return

    if data.startswith("gtt:cancel:"):
        gid = data.split(":", 2)[2]
        handle_gtt_cancel_callback(chat_id, gid, _reply)
        return

    if not data.startswith("exec:"):
        return

    key = data.split(":", 1)[1].strip().lower()
    handle_exec_callback(chat_id, key, _reply)


_PENDING_MODE: dict[str, str] = {}


def _help_text() -> str:
    return (
        "<b>📱 Trading bot commands</b>\n\n"
        "<b>/menu</b> — control panel (strategy, index, lots, positions, GTT)\n"
        "<b>/live</b> — enable REAL Upstox orders (pick strategy + index first)\n"
        "<b>/paper</b> — test mode (pick strategy + index, no real broker orders)\n"
        "<b>/strategy</b> — change today's auto-exec strategy\n"
        "<b>/stop</b> — disable Upstox orders\n"
        "<b>/status</b> — mode + strategy + index + token\n"
        "<b>/news</b> — 24h global + NSE news analysis (Bengali)\n\n"
        "<b>Daily setup</b> — ~8:30 AM IST: Strategy → Index (Nifty/Sensex)\n"
        "<b>GTT points:</b> Nifty SL 15 / Target 30 · Sensex SL 20 / Target 50\n\n"
        "<b>Upstox token (daily before 9:15 AM)</b>\n"
        "<b>/upstox_token</b> eyJ… — paste token from app <b>Generate</b> button\n"
        "<b>/upstox_login</b> — OAuth login link (if Generate fails)\n"
        "<b>/upstox_code</b> — paste redirect URL after login\n\n"
        "<b>/lots 1</b> — option lots (1–10)\n"
        "<b>/help</b> — this message\n\n"
        "<i>Use trading app token (NOT Analytics read-only).\n"
        "Token expires ~3:30 AM IST — refresh each morning, then /menu to configure.</i>"
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

    if cmd in ("/menu",):
        from telegram_control_panel import main_menu_markup, main_menu_text

        _reply(chat_id, main_menu_text(), reply_markup=main_menu_markup())
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
        from upstox_execution_index import get_execution_index
        from upstox_execution_strategy import execution_strategy_label, get_execution_strategy
        from telegram_control_panel import index_picker_markup, index_picker_text

        if not get_execution_strategy():
            _send_strategy_picker(chat_id, mode_hint="paper")
            return
        if not get_execution_index():
            _reply(chat_id, index_picker_text(), reply_markup=index_picker_markup())
            return
        set_mode("paper")
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

    if cmd in ("/news", "/bengali_news", "/bengali"):
        _reply(chat_id, "⏳ ২৪ ঘণ্টার বাজার খবর সংগ্রহ ও বিশ্লেষণ চলছে…")
        try:
            from market_news_analyst import format_bengali_market_news_analysis

            body = format_bengali_market_news_analysis()
            _reply(chat_id, body)
        except Exception as exc:
            logger.exception("Bengali news command failed")
            _reply(chat_id, f"❌ News analysis failed: {html.escape(str(exc)[:200])}")
        return


def poll_telegram_commands() -> int:
    """Process pending Telegram commands. Returns count handled."""
    if not TELEGRAM_COMMANDS_ENABLED or not TELEGRAM_TOKEN:
        return 0

    from config import TELEGRAM_POLL_IN_SESSION

    if _poll_owner_handle is None and not TELEGRAM_POLL_IN_SESSION:
        return 0

    if time.time() < _conflict_backoff_until:
        return 0

    if _poll_owner_handle is not None:
        with _poll_lock:
            return _poll_telegram_commands_locked()

    try:
        with _cross_process_poll_lock():
            with _poll_lock:
                return _poll_telegram_commands_locked()
    except BlockingIOError:
        return 0


def _poll_telegram_commands_locked() -> int:
    offset = _load_offset()
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={
                "offset": offset,
                "timeout": 0,
                "allowed_updates": json.dumps(["message", "callback_query"]),
            },
            timeout=20,
        )
        data = resp.json()
    except requests.RequestException:
        logger.debug("Telegram poll failed", exc_info=True)
        return 0

    if not data.get("ok"):
        desc = str(data.get("description", data))
        if "Conflict" in desc or "terminated by other getUpdates" in desc:
            global _conflict_backoff_until, _last_conflict_log
            now = time.time()
            _conflict_backoff_until = now + 90
            if now - _last_conflict_log > 60:
                _last_conflict_log = now
                logger.warning(
                    "getUpdates conflict — duplicate poller detected (pid=%s). "
                    "On GCP run: pkill -f telegram_command_listener; "
                    "bash scripts/install_gcp_automation.sh",
                    os.getpid(),
                )
            return 0
        logger.warning("Telegram getUpdates error: %s", desc)
        return 0

    handled = 0
    max_id = offset
    allowed = {str(c) for c in telegram_chat_ids()}
    for upd in data.get("result", []):
        max_id = max(max_id, int(upd.get("update_id", 0)) + 1)

        cb = upd.get("callback_query") or {}
        if cb:
            chat_id = _callback_chat_id(cb)
            cb_id = str(cb.get("id", ""))
            cb_data = str(cb.get("data") or "")
            _answer_callback(cb_id)
            if chat_id and _allowed_chat(chat_id):
                try:
                    _handle_callback(chat_id, cb_id, cb_data)
                    handled += 1
                except Exception:
                    logger.exception("Callback failed: %s", cb_data)
                    _reply(
                        chat_id,
                        "❌ <b>Strategy selection failed.</b>\n"
                        "Send <code>/strategy</code> and tap again, or <code>/status</code> to check.",
                    )
            elif chat_id:
                logger.warning(
                    "Ignored callback from chat %s (allowed: %s)",
                    chat_id,
                    ", ".join(sorted(allowed)) if allowed else "(none)",
                )
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
                    "Add this to <code>TELEGRAM_CHAT_ID</code> in .env, then restart bot.",
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


def start_command_poller(interval_sec: float = 2.0) -> threading.Thread | None:
    """Background thread for Telegram commands (only when session polling is enabled)."""
    global _poller_started
    from config import TELEGRAM_POLL_IN_SESSION

    if not TELEGRAM_POLL_IN_SESSION:
        logger.info("Telegram session poller skipped (external listener handles commands).")
        return None
    if _poller_started:
        return None
    _poller_started = True

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
