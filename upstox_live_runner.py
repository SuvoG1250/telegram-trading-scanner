#!/usr/bin/env python3
"""
Always-on Upstox WebSocket + trading session (run on VPS / local PC).

GitHub Actions cannot hold a persistent WebSocket — use this for live data + auto orders.

  python upstox_live_runner.py
  python upstox_live_runner.py --max-minutes 390
  python upstox_live_runner.py --paper   # start in paper mode until /live

Telegram commands (in your bot chat):
  /live   — real Upstox option orders
  /paper  — test mode
  /stop   — disable orders
  /status — check mode
  /help   — all commands
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("upstox_live_runner")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upstox WS + intraday scanner + Telegram commands")
    parser.add_argument("--max-minutes", type=int, default=390)
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Start in paper mode (send /live in Telegram to go live)",
    )
    args = parser.parse_args()

    from telegram_commands import announce_live_runner_start, start_command_poller
    from upstox_trade_state import get_mode, set_mode
    from upstox_websocket import start_upstox_feed, stop_upstox_feed

    set_mode("paper" if args.paper else "live")
    logger.info("Upstox trade mode at start: %s", get_mode())

    start_command_poller(interval_sec=2.0)
    announce_live_runner_start()

    if not start_upstox_feed():
        logger.warning("Upstox WebSocket not started (check UPSTOX_ACCESS_TOKEN).")

    try:
        from session_runner import run_loop

        return run_loop(args.max_minutes)
    finally:
        stop_upstox_feed()


if __name__ == "__main__":
    sys.exit(main())
