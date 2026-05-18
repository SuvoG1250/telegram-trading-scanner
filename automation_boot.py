#!/usr/bin/env python3
"""Send automation boot ping after BOOT_DELAY_MINUTES (see boot_alerts.py)."""

from __future__ import annotations

import logging
import os
import sys

from boot_alerts import try_send_delayed_boot
from config import SEND_BOOT_ALERT
from market_time import is_market_open, is_premarket_window, is_weekday

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("automation_boot")


def main() -> int:
    if not SEND_BOOT_ALERT:
        logger.info("Boot alert disabled.")
        return 0

    event = os.environ.get("GITHUB_EVENT_NAME", "local")
    if not is_weekday():
        if event == "schedule":
            logger.info("Weekend — skipped.")
        return 0

    if event == "schedule" and not is_premarket_window() and not is_market_open():
        logger.info("Outside market hours — skipped.")
        return 0

    if try_send_delayed_boot():
        return 0

    logger.info("Boot not due yet (waits %s min after session start).", os.environ.get("BOOT_DELAY_MINUTES", "30"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
