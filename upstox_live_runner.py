#!/usr/bin/env python3
"""
Always-on Upstox WebSocket + trading session (run on VPS / local PC).

GitHub Actions cannot hold a persistent WebSocket — use this for live data + auto orders.

  python upstox_live_runner.py
  python upstox_live_runner.py --max-minutes 390
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
    parser = argparse.ArgumentParser(description="Upstox WS + intraday scanner loop")
    parser.add_argument("--max-minutes", type=int, default=390)
    args = parser.parse_args()

    from upstox_websocket import start_upstox_feed, stop_upstox_feed

    if not start_upstox_feed():
        logger.warning("Upstox WebSocket not started (check UPSTOX_ACCESS_TOKEN).")

    try:
        from session_runner import run_loop

        return run_loop(args.max_minutes)
    finally:
        stop_upstox_feed()


if __name__ == "__main__":
    sys.exit(main())
