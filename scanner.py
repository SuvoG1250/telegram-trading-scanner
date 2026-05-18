#!/usr/bin/env python3
"""
Intraday NSE stock scanner — daily watchlist, combined strategies, one alert per stock.
"""

from __future__ import annotations

import logging
import os
import sys

from config import (
    LOCK_WATCHLIST_FOR_DAY,
    MIN_STRATEGIES_TO_CONFIRM,
    MIN_TARGET_PROFIT_PCT,
    NO_SIGNAL_STATUS_ON_AUTO_SCAN,
    SCAN_FULL_UNIVERSE,
)
from market_time import is_market_open, is_premarket_window, is_weekday, now_ist
from premarket import build_watchlist, format_watchlist_message
from session_alerts import handle_session_alerts, send_session_start_alert
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner")


def run_premarket() -> list[str]:
    """Pick today's stocks once — locked for the full session."""
    watchlist, ranked = build_watchlist()
    if not watchlist:
        return []
    save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
    send_plain(format_watchlist_message(ranked))
    logger.info("Daily watchlist locked: %s", ", ".join(watchlist))
    return watchlist


def run_intraday_scan(watchlist: list[str]) -> list[Signal]:
    """
    For each watchlist stock: run ALL strategies → confirm → ONE Telegram alert.
    No per-strategy spam; max one combined alert per stock per side per day.
    """
    sent_signals: list[Signal] = []

    for symbol in watchlist:
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
            "CONFIRMED %s %s | strategies=%s | Entry=%s SL=%s Target=%s",
            symbol,
            signal.side,
            ", ".join(confirmed.strategies),
            signal.levels.entry,
            signal.levels.stop_loss,
            signal.levels.primary_target,
        )

        if send_signal(signal):
            mark_sent_combined(symbol, signal.side, confirmed.strategies)
            sent_signals.append(signal)
        else:
            logger.error("Failed to send combined alert for %s", symbol)

    return sent_signals


def _is_automatic_run() -> bool:
    """Any GitHub Actions scan (schedule or task scheduler dispatch)."""
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def _send_no_signal_status(watchlist: list[str]) -> None:
    """Tell user automatic scan completed with no confirmed BUY/SELL."""
    stocks = ", ".join(watchlist) if watchlist else "—"
    text = (
        f"ℹ️ <b>No signal right now</b>\n\n"
        f"🕐 {now_ist().strftime('%d %b %Y, %H:%M IST')}\n"
        f"📋 <b>Scanned:</b> {stocks}\n\n"
        f"All <b>6 strategies</b> checked — need <b>{MIN_STRATEGIES_TO_CONFIRM} of 6</b> agree "
        f"+ min <b>{MIN_TARGET_PROFIT_PCT}%</b> target profit. None qualified.\n"
        f"<i>Next automatic scan in ~5 minutes.</i>"
    )
    if send_plain(text):
        logger.info("No-signal status sent to Telegram.")
    else:
        logger.error("Failed to send no-signal status.")


def _send_scan_summary(signals: list[Signal]) -> None:
    if not signals:
        return
    lines = [
        f"📊 <b>Confirmed Alerts</b> — {now_ist().strftime('%d %b %Y %H:%M IST')}",
        f"✅ <b>{len(signals)} combined signal(s)</b> sent (all strategies checked).\n",
    ]
    for s in signals:
        lv = s.levels
        icon = "🟢" if s.side == "BUY" else "🔴"
        lines.append(
            f"{icon} <b>{s.symbol}</b>\n"
            f"   Entry ₹{lv.entry:,.2f} | SL ₹{lv.stop_loss:,.2f} | Target ₹{lv.primary_target:,.2f}"
        )
    from telegram_client import send_telegram

    send_telegram("\n".join(lines), html_mode=True)


def _needs_full_universe_refresh(watchlist: list[str]) -> bool:
    """Upgrade old small watchlists to full Nifty 100 scan."""
    return SCAN_FULL_UNIVERSE and len(watchlist) < 40


def _get_daily_watchlist() -> list[str]:
    """Load locked daily list — never replace stocks mid-session."""
    watchlist = load_watchlist()

    if _needs_full_universe_refresh(watchlist):
        logger.info("Expanding watchlist to full Nifty 100 scan (%s stocks now).", len(watchlist))
        watchlist, ranked = build_watchlist()
        if watchlist:
            save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
            send_plain(
                f"📌 <b>Scan list updated</b> — now monitoring <b>{len(watchlist)}</b> stocks "
                f"(Nifty 100 + all sectors). 2 of 6 strategies for signals.\n\n"
                + "\n".join(f"• {s}" for s in watchlist[:15])
                + (f"\n<i>…and {len(watchlist) - 15} more</i>" if len(watchlist) > 15 else "")
            )
        return watchlist

    if watchlist and is_watchlist_locked():
        logger.info("Using locked daily watchlist: %s", ", ".join(watchlist))
        return watchlist

    if watchlist:
        save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
        return watchlist

    if is_premarket_window():
        return []

    logger.info("Building initial watchlist (first run today).")
    watchlist, _ = build_watchlist()
    if watchlist:
        save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
        send_plain(
            "📌 <b>Today's watchlist set</b> (locked for the session):\n"
            + "\n".join(f"• {s}" for s in watchlist)
        )
    return watchlist


def main() -> int:
    if not is_weekday():
        logger.info("Market closed (weekend). Exiting.")
        return 0

    logger.info("Scanner run at %s IST", now_ist().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info(
        "Mode: daily watchlist → all strategies → min %s agree → one alert",
        MIN_STRATEGIES_TO_CONFIRM,
    )

    if handle_session_alerts():
        logger.info("Session ended for today. Exiting.")
        return 0

    if is_premarket_window():
        run_premarket()
        return 0

    if not is_market_open():
        logger.info("Outside market hours. Exiting.")
        return 0

    if not session_start_sent():
        send_session_start_alert()

    watchlist = _get_daily_watchlist()
    if not watchlist:
        logger.warning("No watchlist; nothing to scan.")
        return 0

    signals = run_intraday_scan(watchlist)
    if signals:
        _send_scan_summary(signals)
    elif (
        NO_SIGNAL_STATUS_ON_AUTO_SCAN
        and _is_automatic_run()
    ):
        _send_no_signal_status(watchlist)

    logger.info("Scan complete. Combined alerts sent: %d", len(signals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
