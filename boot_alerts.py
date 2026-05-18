"""Delayed automation boot ping (trade signals are always immediate)."""

from __future__ import annotations

import logging
import os

from config import BOOT_DELAY_MINUTES, SEND_BOOT_ALERT
from market_time import is_weekday, now_ist
from state import automation_boot_sent, get_trading_started_at, mark_automation_boot, record_trading_started_at
from telegram_client import send_plain

logger = logging.getLogger(__name__)


def _boot_message() -> str:
    event = os.environ.get("GITHUB_EVENT_NAME", "local")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    link = f"{run_url}/{repo}/actions/runs/{run_id}" if run_id and repo else ""

    trigger_label = {
        "schedule": "GitHub cron (automatic)",
        "workflow_dispatch": "Manual / external cron",
        "repository_dispatch": "External trigger",
    }.get(event, event)

    lines = [
        "🤖 <b>Auto Trading Bot — RUNNING</b>",
        "",
        f"📅 {now_ist().strftime('%d %b %Y, %H:%M IST')}",
        f"⚙️ <b>Trigger:</b> {trigger_label}",
        f"✅ Active {BOOT_DELAY_MINUTES}+ min — scanning Nifty 500 · Chaitu50c signals instant.",
    ]
    if link:
        lines.extend(["", f"🔗 <a href=\"{link}\">View GitHub run</a>"])
    return "\n".join(lines)


def try_send_delayed_boot(*, force_record_start: bool = False) -> bool:
    """
    Send boot ping once per day, only after BOOT_DELAY_MINUTES from session start.
  Trade signals are unaffected and send immediately from the scanner.
    """
    if not SEND_BOOT_ALERT or not is_weekday():
        return False

    if automation_boot_sent():
        return False

    if force_record_start or get_trading_started_at() is None:
        record_trading_started_at()

    started = get_trading_started_at()
    if started is None:
        return False

    elapsed_min = (now_ist() - started).total_seconds() / 60.0
    if elapsed_min < BOOT_DELAY_MINUTES:
        logger.debug(
            "Boot delayed: %.0f / %s min since session start.",
            elapsed_min,
            BOOT_DELAY_MINUTES,
        )
        return False

    if send_plain(_boot_message()):
        mark_automation_boot()
        logger.info("Automation boot sent (%.0f min after session start).", elapsed_min)
        return True

    logger.error("Failed to send delayed automation boot.")
    return False
