"""Session start/stop and end-of-day trade summary."""

from __future__ import annotations

import logging

from config import SEND_DAILY_SUMMARY, SEND_SESSION_ALERTS
from market_time import is_active_session, is_session_stop_window, now_ist
from state import (
    daily_summary_sent,
    mark_daily_summary,
    mark_session_start,
    mark_session_stop,
    record_trading_started_at,
    session_start_sent,
    session_stop_sent,
)
from telegram_client import send_plain
from trade_journal import send_daily_summary

logger = logging.getLogger(__name__)


def _fmt_now() -> str:
    return now_ist().strftime("%d %b %Y, %H:%M IST")


def send_session_start_alert() -> bool:
    if not SEND_SESSION_ALERTS:
        if not session_start_sent():
            mark_session_start()
            record_trading_started_at()
        return True
    text = (
        "🟢 <b>Trading Scanner STARTED</b>\n\n"
        f"📅 {_fmt_now()}\n"
        "📊 Stocks + Nifty + <b>AI</b> (Cerebras, GitHub Models, Groq, Gemini) · BTST 3:20 PM.\n"
        "⏱ Stocks/options: <b>9:26 AM – 3:00 PM</b> · BTST research <b>3:20–3:30 PM</b>.\n"
        "📋 Full day P/L summary after 3:30 PM."
    )
    if send_plain(text):
        mark_session_start()
        record_trading_started_at()
        return True
    return False


def send_session_stop_alert() -> bool:
    if not SEND_SESSION_ALERTS:
        if not session_stop_sent():
            mark_session_stop()
        return True
    text = (
        "🔴 <b>Trading Scanner STOPPED</b>\n\n"
        f"📅 {_fmt_now()}\n"
        "📋 Full day Profit &amp; Loss summary sent above.\n"
        "Next session: next trading day ~9:10 AM IST."
    )
    if send_plain(text):
        mark_session_stop()
        return True
    return False


def send_daily_summary_alert() -> bool:
    if not SEND_DAILY_SUMMARY or daily_summary_sent():
        return False
    if send_daily_summary():
        mark_daily_summary()
        return True
    return False


def handle_session_alerts() -> bool:
    """After 3:30 PM: daily summary + session stop. Returns True to exit scan."""
    if is_session_stop_window():
        if not daily_summary_sent():
            send_daily_summary_alert()
        if not session_stop_sent():
            send_session_stop_alert()
        return True

    if is_active_session() and not session_start_sent():
        send_session_start_alert()

    return False
