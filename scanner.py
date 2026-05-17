#!/usr/bin/env python3
"""
Intraday NSE stock scanner with Telegram alerts.

Designed for scheduled runs (e.g. GitHub Actions every 5 minutes).
Part 1 runs in the pre-market window (~9:10–9:25 IST).
Part 2 scans the watchlist with three parallel strategies during market hours.
"""

from __future__ import annotations

import logging
import sys

from market_time import is_market_open, is_premarket_window, is_weekday, now_ist
from premarket import build_watchlist
from state import already_sent, load_watchlist, mark_sent, save_watchlist
from strategies import STRATEGY_SCANNERS
from telegram_client import send_plain, send_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner")


def run_premarket() -> list[str]:
    watchlist = build_watchlist()
    if watchlist:
        save_watchlist(watchlist)
        send_plain(
            f"📋 Pre-Market Watchlist ({now_ist().strftime('%d %b %Y %H:%M IST')})\n"
            + "\n".join(f"• {s}" for s in watchlist)
        )
    return watchlist


def run_intraday_scan(watchlist: list[str]) -> int:
    alerts = 0
    for symbol in watchlist:
        for scan_fn in STRATEGY_SCANNERS:
            try:
                signal = scan_fn(symbol)
            except Exception:
                logger.exception("Strategy %s failed for %s", scan_fn.__name__, symbol)
                continue
            if signal is None:
                continue
            if already_sent(signal.symbol, signal.strategy, signal.side):
                continue
            if send_signal(signal):
                mark_sent(signal.symbol, signal.strategy, signal.side)
                alerts += 1
                logger.info(
                    "Alert sent: %s %s %s",
                    signal.symbol,
                    signal.strategy,
                    signal.side,
                )
    return alerts


def main() -> int:
    if not is_weekday():
        logger.info("Market closed (weekend). Exiting.")
        return 0

    ist_now = now_ist()
    logger.info("Scanner run at %s IST", ist_now.strftime("%Y-%m-%d %H:%M:%S"))

    if is_premarket_window():
        run_premarket()
        return 0

    if not is_market_open():
        logger.info("Outside market hours. Exiting.")
        return 0

    watchlist = load_watchlist()
    if not watchlist:
        logger.info("No watchlist for today; building from pre-market filters.")
        watchlist = build_watchlist()
        if watchlist:
            save_watchlist(watchlist)

    if not watchlist:
        logger.warning("Empty watchlist; nothing to scan.")
        return 0

    sent = run_intraday_scan(watchlist)
    logger.info("Scan complete. New alerts: %d", sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
