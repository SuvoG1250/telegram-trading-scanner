#!/usr/bin/env python3
"""Poll Telegram bot commands for a fixed duration (used by GitHub Actions + cron)."""

from __future__ import annotations

import argparse
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

    from config import TELEGRAM_COMMANDS_ENABLED, TELEGRAM_TOKEN
    from telegram_commands import poll_telegram_commands

    if not TELEGRAM_COMMANDS_ENABLED or not TELEGRAM_TOKEN:
        logger.warning("Telegram commands disabled or TELEGRAM_TOKEN missing.")
        return 0

    try:
        import requests

        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "false"},
            timeout=20,
        )
    except Exception:
        logger.debug("deleteWebhook failed", exc_info=True)

    deadline = time.time() + max(10, args.seconds)
    total = 0
    logger.info("Telegram command listener started for %s seconds.", args.seconds)
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
