#!/usr/bin/env python3
"""
Run scanner every 5 minutes inside one GitHub Actions job.
Uses 2 scheduled triggers per day (morning + afternoon) instead of */5 cron,
which is unreliable on many GitHub accounts.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from market_time import (
    is_global_alert_window,
    is_market_open,
    is_premarket_window,
    is_session_stop_window,
    is_weekday,
    now_ist,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("session_runner")

SCAN_INTERVAL_SEC = 180


def _should_continue() -> bool:
    if is_global_alert_window():
        return True
    if not is_weekday():
        return False
    if is_premarket_window():
        return True
    if is_market_open():
        return True
    if is_session_stop_window():
        from state import daily_summary_sent, session_stop_sent

        return not daily_summary_sent() or not session_stop_sent()
    return False


def run_loop(max_minutes: int) -> int:
    from scanner import main as scan_once

    deadline = time.time() + max_minutes * 60
    iteration = 0

    import os

    event = os.environ.get("GITHUB_EVENT_NAME", "local")
    logger.info(
        "Session loop started | trigger=%s | max %s min | IST %s",
        event,
        max_minutes,
        now_ist().strftime("%H:%M:%S"),
    )

    while time.time() < deadline:
        if not _should_continue():
            logger.info("Outside trading window — ending loop.")
            break

        iteration += 1
        logger.info("=== Scan iteration %s ===", iteration)
        try:
            scan_once()
        except Exception:
            logger.exception("Scan iteration %s failed", iteration)

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sleep_for = min(SCAN_INTERVAL_SEC, remaining)
        logger.info("Sleeping %.0f seconds until next scan...", sleep_for)
        time.sleep(sleep_for)

    from session_alerts import handle_session_alerts

    # After 3:30 PM IST: full-day P/L summary + session stop (once per day)
    handle_session_alerts()

    logger.info("Session loop finished after %s iterations.", iteration)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-minutes", type=int, default=320)
    args = parser.parse_args()
    sys.exit(run_loop(args.max_minutes))
