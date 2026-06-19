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
    is_eod_summary_due,
    is_global_alert_window,
    is_market_open,
    is_premarket_window,
    is_weekday,
    ist_time_tuple,
    now_ist,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("session_runner")

SCAN_INTERVAL_SEC = 180


def _ensure_index_btst_sent() -> None:
    """Safety net if the 3:10–3:29 PM window was missed during the scan loop."""
    if not is_weekday() or not is_market_open():
        return
    from config import NIFTY_BTST_ENABLED, SENSEX_BTST_ENABLED

    t = ist_time_tuple()
    if t < (15, 15):
        return

    from state import nifty_btst_sent, sensex_btst_sent

    if NIFTY_BTST_ENABLED and not nifty_btst_sent():
        from nifty_btst import run_nifty_btst_alert

        logger.info("BTST safety net — running Nifty BTST.")
        run_nifty_btst_alert(force=True)
    if SENSEX_BTST_ENABLED and not sensex_btst_sent():
        from sensex_btst import run_sensex_btst_alert

        logger.info("BTST safety net — running Sensex BTST.")
        run_sensex_btst_alert(force=True)


def _should_continue() -> bool:
    if is_global_alert_window():
        return True
    if not is_weekday():
        return False
    if is_premarket_window():
        return True
    if is_market_open():
        return True
    if is_eod_summary_due():
        from state import daily_summary_sent, session_stop_sent

        return not daily_summary_sent() or not session_stop_sent()
    return False


def run_loop(max_minutes: int) -> int:
    from config import TELEGRAM_POLL_IN_SESSION
    from scanner import main as scan_once
    from telegram_commands import (
        announce_automation_session,
        poll_telegram_commands,
        start_command_poller,
    )
    from upstox_live_feed import prepare_live_feed
    from upstox_websocket import stop_upstox_feed

    start_command_poller(interval_sec=2.0)
    if TELEGRAM_POLL_IN_SESSION:
        poll_telegram_commands()
    prepare_live_feed()
    if TELEGRAM_POLL_IN_SESSION:
        poll_telegram_commands()
    announce_automation_session()
    iteration = 0
    try:
        deadline = time.time() + max_minutes * 60

        import os

        event = os.environ.get("GITHUB_EVENT_NAME", "local")
        logger.info(
            "Session loop started | trigger=%s | max %s min | IST %s | telegram_poll=%s",
            event,
            max_minutes,
            now_ist().strftime("%H:%M:%S"),
            TELEGRAM_POLL_IN_SESSION,
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

        _ensure_index_btst_sent()
        handle_session_alerts()
    finally:
        stop_upstox_feed()

    logger.info("Session loop finished after %s iterations.", iteration)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-minutes", type=int, default=320)
    args = parser.parse_args()
    sys.exit(run_loop(args.max_minutes))
