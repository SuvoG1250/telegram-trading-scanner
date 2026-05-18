#!/usr/bin/env python3
"""
Intraday NSE stock scanner with professional Telegram trade alerts.
"""

from __future__ import annotations

import logging
import sys

from market_time import is_market_open, is_premarket_window, is_weekday, now_ist
from premarket import build_watchlist, format_watchlist_message
from session_alerts import handle_session_alerts, send_session_start_alert
from state import already_sent, load_watchlist, mark_sent, save_watchlist, session_start_sent
from strategies import STRATEGY_NAMES, STRATEGY_SCANNERS
from telegram_client import Signal, send_plain, send_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner")


def _log_signal_preview(signal: Signal) -> None:
    lv = signal.levels
    logger.info(
        "CONFIRMED %s | %s %s | Entry=₹%s SL=₹%s BestTarget=₹%s R:R=1:%s",
        signal.strategy,
        signal.symbol,
        signal.side,
        lv.entry,
        lv.stop_loss,
        lv.primary_target,
        lv.risk_reward_best,
    )


def run_premarket() -> list[str]:
    watchlist, ranked = build_watchlist()
    if not watchlist:
        return []
    save_watchlist(watchlist)
    send_plain(format_watchlist_message(ranked))
    return watchlist


def run_intraday_scan(watchlist: list[str]) -> list[Signal]:
    """Run all strategies; return list of signals sent."""
    sent_signals: list[Signal] = []

    for symbol in watchlist:
        for scan_fn, name in zip(STRATEGY_SCANNERS, STRATEGY_NAMES):
            try:
                signal = scan_fn(symbol)
            except Exception:
                logger.exception("Strategy %s failed for %s", name, symbol)
                continue

            if signal is None:
                continue

            if already_sent(signal.symbol, signal.strategy, signal.side):
                continue

            _log_signal_preview(signal)

            if send_signal(signal):
                mark_sent(signal.symbol, signal.strategy, signal.side)
                sent_signals.append(signal)
            else:
                logger.error("Failed to send Telegram for %s %s", symbol, name)

    return sent_signals


def _send_scan_summary(signals: list[Signal]) -> None:
    if not signals:
        return
    lines = [
        f"📊 <b>Scan Summary</b> — {now_ist().strftime('%d %b %Y %H:%M IST')}",
        f"✅ <b>{len(signals)} new signal(s)</b> confirmed & sent.\n",
    ]
    for s in signals:
        lv = s.levels
        icon = "🟢" if s.side == "BUY" else "🔴"
        lines.append(
            f"{icon} <b>{s.symbol}</b> | {s.strategy}\n"
            f"   Entry ₹{lv.entry:,.2f} → Target ₹{lv.primary_target:,.2f} | SL ₹{lv.stop_loss:,.2f}"
        )
    from telegram_client import send_telegram

    send_telegram("\n".join(lines), html_mode=True)


def main() -> int:
    if not is_weekday():
        logger.info("Market closed (weekend). Exiting.")
        return 0

    logger.info("Scanner run at %s IST", now_ist().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Active strategies: %s", ", ".join(STRATEGY_NAMES))

    if handle_session_alerts():
        logger.info("Session ended for today. Exiting.")
        return 0

    if is_premarket_window():
        run_premarket()

    if not is_market_open():
        logger.info("Outside market hours. Exiting.")
        return 0

    if not session_start_sent():
        send_session_start_alert()

    watchlist = load_watchlist()
    if not watchlist:
        logger.info("No watchlist for today; building from filters.")
        watchlist, _ = build_watchlist()
        if watchlist:
            save_watchlist(watchlist)

    if not watchlist:
        logger.warning("Empty watchlist; using fallback.")
        watchlist, _ = build_watchlist()
        if watchlist:
            save_watchlist(watchlist)
            send_plain(
                f"⚠️ Watchlist was empty — using fallback list for today:\n"
                + ", ".join(watchlist)
            )

    if not watchlist:
        logger.warning("Empty watchlist; nothing to scan.")
        return 0

    signals = run_intraday_scan(watchlist)
    if signals:
        _send_scan_summary(signals)

    logger.info("Scan complete. Confirmed signals sent: %d", len(signals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
