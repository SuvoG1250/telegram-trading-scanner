"""Once-per-day Telegram alerts when the scanner session starts and stops."""

from __future__ import annotations

import logging

from market_time import is_active_session, is_session_stop_window, now_ist
from state import mark_session_start, mark_session_stop, session_start_sent, session_stop_sent
from telegram_client import send_plain

logger = logging.getLogger(__name__)


def _fmt_now() -> str:
    return now_ist().strftime("%d %b %Y, %H:%M IST")


def send_session_start_alert() -> bool:
    text = (
        "🟢 Trading Scanner STARTED\n\n"
        f"📅 {_fmt_now()}\n"
        "⏰ Session: Pre-market (9:10–9:25) + Intraday (9:15 AM–3:30 PM IST)\n"
        "📊 Strategies: Winning Combo | 15m ORB | Gap Breakout\n\n"
        "Trade signal alerts will be sent to this chat when conditions match."
    )
    if send_plain(text):
        mark_session_start()
        logger.info("Session start alert sent.")
        return True
    return False


def send_session_stop_alert() -> bool:
    text = (
        "🔴 Trading Scanner STOPPED\n\n"
        f"📅 {_fmt_now()}\n"
        "✅ Today's NSE intraday session has ended.\n\n"
        "Next automatic start: next trading day ~9:10 AM IST."
    )
    if send_plain(text):
        mark_session_stop()
        logger.info("Session stop alert sent.")
        return True
    return False


def handle_session_alerts() -> bool:
    """
    Send start/stop alerts once per day.
    Returns True if the run should exit early (after stop alert).
    """
    if is_session_stop_window() and not session_stop_sent():
        send_session_stop_alert()
        return True

    if is_active_session() and not session_start_sent():
        send_session_start_alert()

    return False
