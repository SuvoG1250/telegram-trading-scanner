#!/usr/bin/env python3
"""Poll Telegram bot commands for a fixed duration (used by GCP daemon + GitHub Actions)."""

from __future__ import annotations

import argparse
import atexit
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telegram_command_listener")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seconds",
        type=int,
        default=100,
        help="How long to poll getUpdates (default 100s)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.5,
        help="Seconds between polls (default 1.5)",
    )
    args = parser.parse_args()

    from config import telegram_commands_status
    from telegram_commands import (
        acquire_telegram_poll_ownership,
        poll_telegram_commands,
        release_telegram_poll_ownership,
    )

    tg_ok, tg_msg = telegram_commands_status()
    if not tg_ok:
        logger.error("Cannot start listener: %s | .env=%s", tg_msg, ROOT / ".env")
        return 1

    if not acquire_telegram_poll_ownership():
        logger.error("Another Telegram command listener is already running — exiting.")
        return 1

    atexit.register(release_telegram_poll_ownership)

    try:
        import requests
        from config import TELEGRAM_TOKEN

        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "false"},
            timeout=20,
        )
    except Exception:
        logger.debug("deleteWebhook failed", exc_info=True)

    deadline = time.time() + max(10, args.seconds)
    total = 0
    logger.info("Telegram command listener started for %s seconds (exclusive poll owner).", args.seconds)
    while time.time() < deadline:
        try:
            n = poll_telegram_commands()
            total += n
        except Exception:
            logger.exception("Poll iteration failed")
        time.sleep(max(0.5, args.interval))

    logger.info("Telegram command listener finished — handled %s command(s).", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
