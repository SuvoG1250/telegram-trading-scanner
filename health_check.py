"""Once-per-day morning health check Telegram (bot + API status)."""

from __future__ import annotations

import logging
import os

import requests

from config import (
    AI_SIGNAL_VALIDATE,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    MAX_STOCK_PRICE,
    MIN_STOCK_PRICE,
    SCAN_STRATEGIES,
    SEND_HEALTH_CHECK,
    TELEGRAM_TOKEN,
)
from gemini_client import gemini_generate, llm_available
from groq_client import groq_generate
from market_time import now_ist
from state import health_check_sent, load_watchlist, mark_health_check_sent
from telegram_client import send_plain, telegram_chat_ids

logger = logging.getLogger(__name__)


def _icon(ok: bool | None) -> str:
    if ok is True:
        return "✅"
    if ok is False:
        return "❌"
    return "➖"


def _check_telegram() -> tuple[bool | None, str]:
    if not TELEGRAM_TOKEN:
        return False, "token missing"
    chats = telegram_chat_ids()
    if not chats:
        return False, "chat id missing"
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe",
            timeout=8,
        )
        if r.ok:
            name = r.json().get("result", {}).get("username", "bot")
            return True, f"@{name} · {len(chats)} chat(s)"
        return False, "getMe failed"
    except requests.RequestException as exc:
        return False, str(exc)[:40]


def _check_gemini() -> tuple[bool | None, str]:
    if not GEMINI_API_KEY:
        return None, "not configured"
    text = gemini_generate("Reply OK only.", max_tokens=8, temperature=0.0)
    if text:
        return True, "responding"
    return False, "quota/error (use Groq fallback)"


def _check_groq() -> tuple[bool | None, str]:
    if not GROQ_API_KEY:
        return None, "not configured"
    text = groq_generate("Reply OK only.", max_tokens=8, temperature=0.0)
    if text:
        return True, "responding"
    return False, "blocked or invalid key"


def _check_broker(name: str, configured: bool, extra: str = "") -> tuple[bool | None, str]:
    if not configured:
        return None, "not configured"
    detail = extra or "credentials set"
    return True, detail


def build_health_report() -> str:
    from dhan_client import dhan_configured
    from fyers_client import fyers_configured
    from strategies import EQUITY_STRATEGY_LABELS
    from upstox_client import upstox_configured

    ts = now_ist().strftime("%d %b %Y, %H:%M IST")
    host = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"

    tg_ok, tg_detail = _check_telegram()
    gem_ok, gem_detail = _check_gemini()
    groq_ok, groq_detail = _check_groq()
    up_ok, up_detail = _check_broker("Upstox", upstox_configured())
    dh_ok, dh_detail = _check_broker("Dhan", dhan_configured())
    fy_ok, fy_detail = _check_broker("Fyers", fyers_configured())

    wl = load_watchlist()
    wl_n = len(wl)
    strat_n = len(EQUITY_STRATEGY_LABELS)
    ai_on = llm_available() and AI_SIGNAL_VALIDATE

    lines = [
        "🩺 <b>Morning health check</b>",
        f"📅 {ts}",
        f"🤖 <b>Scanner:</b> OK · running on <i>{host}</i>",
        "",
        "<b>API status</b>",
        f"{_icon(tg_ok)} <b>Telegram</b> — {tg_detail}",
        f"{_icon(gem_ok)} <b>Gemini</b> — {gem_detail}",
        f"{_icon(groq_ok)} <b>Groq</b> — {groq_detail}",
        f"{_icon(up_ok)} <b>Upstox</b> — {up_detail}",
        f"{_icon(dh_ok)} <b>Dhan</b> — {dh_detail}",
        f"{_icon(fy_ok)} <b>Fyers</b> — {fy_detail}",
        "",
        "<b>Session config</b>",
        f"📋 Watchlist: <b>{wl_n}</b> symbols · Rs {MIN_STOCK_PRICE:.0f}–{MAX_STOCK_PRICE:.0f}",
        f"📊 Strategies: <b>{strat_n}</b> ({SCAN_STRATEGIES}) · AI filter: <b>{'ON' if ai_on else 'OFF'}</b>",
        "⏱ Scan every <b>3 min</b> · BTST <b>3:20 PM</b> · P/L after <b>3:30 PM</b>",
        "",
        "<i>Trade alerts follow when setups match. This ping only confirms the bot is alive.</i>",
    ]
    return "\n".join(lines)


def send_morning_health_check() -> bool:
    if not SEND_HEALTH_CHECK:
        return False
    if health_check_sent():
        return False
    text = build_health_report()
    if send_plain(text):
        mark_health_check_sent()
        logger.info("Morning health check sent.")
        return True
    logger.error("Morning health check Telegram send failed.")
    return False
