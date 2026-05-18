#!/usr/bin/env python3
"""Send once-per-day Telegram proof that the automated runner started."""

from __future__ import annotations

import logging
import os
import sys

from market_time import is_weekday, now_ist
from state import automation_boot_sent, mark_automation_boot
from telegram_client import send_plain

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("automation_boot")


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "local")
    if not is_weekday():
        if event == "schedule":
            logger.info("Weekend — scheduled run skipped.")
        return 0

    if automation_boot_sent():
        logger.info("Automation boot already sent today.")
        return 0

    from market_time import is_market_open, is_premarket_window

    if event == "schedule" and not is_premarket_window() and not is_market_open():
        logger.info("Outside market hours — skip boot message.")
        return 0

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
        "🔄 Scans every 5 min until market close.",
        "📋 Watchlist locked for the day | 3+ strategies → one BUY/SELL alert.",
    ]
    if link:
        lines.extend(["", f"🔗 <a href=\"{link}\">View GitHub run</a>"])

    if send_plain("\n".join(lines)):
        mark_automation_boot()
        logger.info("Automation boot alert sent (%s).", trigger_label)
        return 0

    logger.error("Failed to send automation boot alert.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
