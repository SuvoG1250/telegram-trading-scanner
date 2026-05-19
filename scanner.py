#!/usr/bin/env python3
"""Intraday NSE scanner — sends Telegram trade signals only (default)."""

from __future__ import annotations

import logging
import os
import sys

from boot_alerts import try_send_delayed_boot
from config import (
    LOCK_WATCHLIST_FOR_DAY,
    MIN_STOCK_MOVE_POTENTIAL_PCT,
    MIN_TARGET_PROFIT_PCT,
    NO_SIGNAL_STATUS_ON_AUTO_SCAN,
    REQUIRE_FNO_ELIGIBLE,
    SCAN_FULL_UNIVERSE,
    SCAN_STRATEGIES,
    SEND_PREMARKET_REPORT,
    SIGNALS_ONLY_TELEGRAM,
    USE_TRADE_FILTERS,
)
from trade_filters import filter_symbols, passes_trade_filters
from data_fetcher import clear_session_cache
from market_time import is_market_open, is_new_trade_window, is_premarket_window, is_weekday, now_ist
from premarket import build_watchlist, format_watchlist_message
from session_alerts import handle_session_alerts, send_session_start_alert
from state import record_trading_started_at
from signal_aggregator import collect_raw_signals, confirm_signals
from state import (
    already_sent_combined,
    is_watchlist_locked,
    load_watchlist,
    mark_sent_combined,
    save_watchlist,
    session_start_sent,
)
from strategies import STRATEGY_NAMES, STRATEGY_SCANNERS
from telegram_client import Signal, send_plain, send_signal
from trade_journal import record_trade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner")


def run_premarket() -> list[str]:
    """Build today's scan list; send pre-market report when enabled."""
    watchlist, ranked = build_watchlist()
    if not watchlist:
        return []
    save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
    logger.info("Watchlist ready: %s symbols", len(watchlist))
    if SEND_PREMARKET_REPORT:
        send_plain(format_watchlist_message(ranked))
    return watchlist


def run_intraday_scan(watchlist: list[str]) -> list[Signal]:
    clear_session_cache()
    sent_signals: list[Signal] = []

    for symbol in watchlist:
        if USE_TRADE_FILTERS:
            ok, reason = passes_trade_filters(symbol)
            if not ok:
                logger.debug("Skip %s — %s", symbol, reason)
                continue
        raw = collect_raw_signals(symbol, STRATEGY_SCANNERS, STRATEGY_NAMES)
        if not raw:
            continue

        confirmed = confirm_signals(raw)
        if confirmed is None:
            continue

        signal = confirmed.to_telegram_signal()

        if signal.kind == "EXIT":
            if already_sent_combined(symbol, signal.side):
                continue
        elif already_sent_combined(symbol, signal.side):
            continue

        logger.info(
            "SIGNAL %s %s | Entry=%s SL=%s Target=%s",
            symbol,
            signal.side,
            signal.levels.entry,
            signal.levels.stop_loss,
            signal.levels.primary_target,
        )

        if send_signal(signal):
            mark_sent_combined(symbol, signal.side, confirmed.strategies)
            record_trade(signal)
            sent_signals.append(signal)
        else:
            logger.error("Failed to send signal for %s", symbol)

    return sent_signals


def run_nifty_options_scan() -> Signal | None:
    """Supertrend flip on Nifty → Buy CE/PE (once per side per day)."""
    from nifty_options import STRATEGY_NAME, scan_nifty_supertrend_option
    from state import already_sent, mark_sent

    sig = scan_nifty_supertrend_option()
    if sig is None:
        return None
    if already_sent("NIFTY", STRATEGY_NAME, sig.side):
        logger.info("Skip NIFTY option — already sent %s today.", sig.side)
        return None
    if send_signal(sig):
        mark_sent("NIFTY", STRATEGY_NAME, sig.side)
        record_trade(sig)
        logger.info("NIFTY option signal sent: %s", sig.side)
        return sig
    logger.error("Failed to send NIFTY option signal.")
    return None


def _is_automatic_run() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def _needs_full_universe_refresh(watchlist: list[str]) -> bool:
    return SCAN_FULL_UNIVERSE and len(watchlist) < 200


def _get_daily_watchlist() -> list[str]:
    watchlist = load_watchlist()

    if _needs_full_universe_refresh(watchlist):
        logger.info("Refreshing scan list to full universe.")
        watchlist, _ = build_watchlist()
        if watchlist:
            save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
        return watchlist

    if watchlist and is_watchlist_locked():
        return watchlist

    if watchlist:
        save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
        return watchlist

    if is_premarket_window():
        return []

    logger.info("Building initial watchlist.")
    watchlist, _ = build_watchlist()
    if watchlist:
        save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
    return watchlist


def main() -> int:
    if not is_weekday():
        logger.info("Weekend — no scan.")
        return 0

    mode = "signals-only" if SIGNALS_ONLY_TELEGRAM else "verbose"
    logger.info(
        "Scan %s IST | %s | strategy=%s | filters=%s F&O=%s | min move %.1f%% | opt target %.1f%%",
        now_ist().strftime("%H:%M"),
        mode,
        SCAN_STRATEGIES,
        USE_TRADE_FILTERS,
        REQUIRE_FNO_ELIGIBLE,
        MIN_STOCK_MOVE_POTENTIAL_PCT,
        MIN_TARGET_PROFIT_PCT,
    )

    if handle_session_alerts():
        logger.info("Session ended.")
        return 0

    if is_premarket_window():
        run_premarket()
        return 0

    if not is_market_open():
        logger.info("Market closed.")
        return 0

    if not is_new_trade_window():
        logger.info("After 3:00 PM IST — no new trades (summary at 3:30 PM).")
        return 0

    if not session_start_sent():
        send_session_start_alert()
    else:
        record_trading_started_at()

    watchlist = _get_daily_watchlist()
    if not watchlist:
        logger.warning("Empty watchlist.")
        return 0

    signals = run_intraday_scan(watchlist)
    nifty_sig = run_nifty_options_scan()
    if nifty_sig:
        signals.append(nifty_sig)

    try_send_delayed_boot()

    if (
        not signals
        and NO_SIGNAL_STATUS_ON_AUTO_SCAN
        and _is_automatic_run()
    ):
        send_plain(
            f"No signal — {now_ist().strftime('%H:%M IST')}"
        )

    logger.info("Done. Signals sent: %d / %d symbols", len(signals), len(watchlist))
    return 0


if __name__ == "__main__":
    sys.exit(main())
