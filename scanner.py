#!/usr/bin/env python3
"""Intraday NSE scanner — sends Telegram trade signals only (default)."""

from __future__ import annotations

import logging
import os
import sys

import html as html_module

from boot_alerts import try_send_delayed_boot
from config import (
    LOCK_WATCHLIST_FOR_DAY,
    MAX_BUY_ALERTS_PER_SCAN,
    MAX_DAILY_BUY_SIGNALS,
    MAX_SELL_ALERTS_PER_SCAN,
    MIN_STOCK_MOVE_POTENTIAL_PCT,
    MIN_STRATEGIES_TO_CONFIRM,
    MIN_TARGET_PROFIT_PCT,
    REQUIRE_FNO_ELIGIBLE,
    SCAN_FULL_UNIVERSE,
    SCAN_STRATEGIES,
    SEND_PREMARKET_REPORT,
    SIGNALS_ONLY_TELEGRAM,
    SLTP_CLOSE_ALERT_TELEGRAM,
    USE_TRADE_FILTERS,
)
from trade_filters import filter_symbols, passes_trade_filters
from data_fetcher import clear_session_cache
from market_time import is_market_open, is_new_trade_window, is_premarket_window, is_weekday, now_ist
from premarket import build_watchlist, format_watchlist_message
from session_alerts import handle_session_alerts
from state import record_trading_started_at
from position_lifecycle import (
    caption_after_prior_exit,
    dismiss_option_exit_flag,
    dismiss_stock_exit_flag,
    equity_buy_sent_today,
    equity_candidate_score,
    equity_position_open,
    increment_equity_buy_sent,
    peek_option_exit_flag,
    peek_stock_exit_flag,
    premium_position_open,
    register_equity_open,
    register_premium_open,
    reconcile_all_positions,
)
from signal_aggregator import (
    collect_raw_signals,
    confirm_signals,
    confirm_single_signal,
    ConfirmedSignal,
)
from state import (
    is_watchlist_locked,
    load_watchlist,
    save_watchlist,
)
from strategies import STRATEGY_NAMES, STRATEGY_SCANNERS
from scan_summary import ScanStats
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


def run_intraday_scan(watchlist: list[str]) -> tuple[list[Signal], ScanStats]:
    clear_session_cache()
    sent_signals: list[Signal] = []
    raw_candidates: list[tuple[str, ConfirmedSignal, Signal, float]] = []
    stats = ScanStats(symbols_scanned=len(watchlist))

    for symbol in watchlist:
        if USE_TRADE_FILTERS:
            ok, reason = passes_trade_filters(symbol)
            if not ok:
                logger.debug("Skip %s — %s", symbol, reason)
                continue
        if equity_position_open(symbol):
            logger.debug("Skip %s — active equity plan still awaiting SL/Target.", symbol)
            continue

        stats.symbols_checked += 1
        raw = collect_raw_signals(symbol, STRATEGY_SCANNERS, STRATEGY_NAMES)
        if not raw:
            continue

        if MIN_STRATEGIES_TO_CONFIRM <= 1:
            for sig in raw:
                confirmed = confirm_single_signal(sig)
                if confirmed is None:
                    continue
                telegram_sig = confirmed.to_telegram_signal()
                strat = confirmed.strategies[0]
                score = equity_candidate_score(telegram_sig)
                raw_candidates.append((symbol, confirmed, telegram_sig, score))
        else:
            confirmed = confirm_signals(raw)
            if confirmed is None:
                continue
            telegram_sig = confirmed.to_telegram_signal()
            strat = " + ".join(confirmed.strategies)
            score = equity_candidate_score(telegram_sig)
            raw_candidates.append((symbol, confirmed, telegram_sig, score))

    best_per_symbol: dict[str, tuple[ConfirmedSignal, float, str]] = {}
    for symbol, confirmed, telegram_sig, score in raw_candidates:
        cur = best_per_symbol.get(symbol)
        strat = " + ".join(confirmed.strategies)
        if cur is None or score > cur[1]:
            best_per_symbol[symbol] = (confirmed, score, strat)

    ranked = sorted(best_per_symbol.values(), key=lambda t: -t[1])
    buy_scan_emitted = 0
    sell_scan_emitted = 0
    daily_buy_left = max(0, MAX_DAILY_BUY_SIGNALS - equity_buy_sent_today())
    logger.info(
        "Equity scan: %d raw setups, %d symbols ranked | BUY %d/%d day left · up to %d/scan · SELL up to %d/scan",
        len(raw_candidates),
        len(best_per_symbol),
        daily_buy_left,
        MAX_DAILY_BUY_SIGNALS,
        MAX_BUY_ALERTS_PER_SCAN,
        MAX_SELL_ALERTS_PER_SCAN,
    )

    for confirmed, score, strat in ranked:
        side = confirmed.side
        symbol = confirmed.symbol

        if side == "BUY":
            if buy_scan_emitted >= MAX_BUY_ALERTS_PER_SCAN or daily_buy_left <= 0:
                continue
        else:
            if sell_scan_emitted >= MAX_SELL_ALERTS_PER_SCAN:
                continue

        telegram_sig = confirmed.to_telegram_signal()
        peek = peek_stock_exit_flag(symbol)
        reentry = caption_after_prior_exit(peek, basket="equity")
        if reentry:
            telegram_sig.note = (
                f"{telegram_sig.note}\n{reentry}".strip() if telegram_sig.note else reentry
            )
        logger.info(
            "SIGNAL %s %s [%s] score=%.1f | Entry=%s SL=%s Target=%s",
            symbol,
            telegram_sig.side,
            strat,
            score,
            telegram_sig.levels.entry,
            telegram_sig.levels.stop_loss,
            telegram_sig.levels.primary_target,
        )

        ok = send_signal(telegram_sig)
        if ok:
            register_equity_open(telegram_sig, strat)
            record_trade(telegram_sig)
            if telegram_sig.side == "BUY":
                increment_equity_buy_sent()
                buy_scan_emitted += 1
                daily_buy_left -= 1
            else:
                sell_scan_emitted += 1
            if peek is not None:
                dismiss_stock_exit_flag(symbol)
            sent_signals.append(telegram_sig)
        else:
            logger.error("Failed to send signal for %s [%s]", symbol, strat)

    stats.raw_setups = len(raw_candidates)
    stats.confirmed_ranked = len(best_per_symbol)
    stats.buy_sent = sum(1 for s in sent_signals if s.side == "BUY")
    stats.sell_sent = sum(1 for s in sent_signals if s.side == "SELL")
    return sent_signals, stats


def run_nifty_options_scan() -> Signal | None:
    """Supertrend flip on Nifty → Buy CE/PE; blocked until prior premium exits."""
    sig = scan_nifty_supertrend_option_lookup()
    if sig is None:
        return None
    if premium_position_open(sig.side):
        logger.info("Skip Nifty option (%s) — premium plan still awaiting SL/Target.", sig.side)
        return None

    peek = peek_option_exit_flag(sig.side)
    sig.note = caption_after_prior_exit(peek, basket="option")

    ok = send_signal(sig)
    if ok:
        register_premium_open(sig)
        record_trade(sig)
        if peek is not None:
            dismiss_option_exit_flag(sig.side)
        logger.info("NIFTY option signal sent: %s", sig.side)
        return sig
    logger.error("Failed to send NIFTY option signal.")
    return None


def scan_nifty_supertrend_option_lookup() -> Signal | None:
    """Lazy import avoids circular refs during tooling."""
    from nifty_options import scan_nifty_supertrend_option

    return scan_nifty_supertrend_option()


def broadcast_lifecycle_updates(equity_hits: list, premium_hits: list) -> None:
    if not SLTP_CLOSE_ALERT_TELEGRAM:
        return
    for symbol, strat, reason in equity_hits:
        label = "✅ TARGET" if reason == "TARGET_HIT" else "⚠️ STOP LOSS"
        send_plain(
            f"{label} cleared <b>{html_module.escape(symbol)}</b> "
            f"({html_module.escape(strat)}) — awaiting fresh scanner ideas.",
            html_mode=True,
        )
    for side_label, reason in premium_hits:
        label = "✅ TARGET" if reason == "TARGET_HIT" else "⚠️ STOP LOSS"
        send_plain(
            f"{label} cleared premium <b>{html_module.escape(side_label)}</b> — scanners may relist.",
            html_mode=True,
        )


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
    from strategies import EQUITY_STRATEGY_LABELS

    strat_mode = (
        f"all ({len(EQUITY_STRATEGY_LABELS)} setups)"
        if SCAN_STRATEGIES == "all"
        else SCAN_STRATEGIES
    )
    logger.info(
        "Scan %s IST | %s | strategies=%s | filters=%s F&O=%s | min move %.1f%% | opt target %.1f%%",
        now_ist().strftime("%H:%M"),
        mode,
        strat_mode,
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

    from config import NIFTY_BTST_ENABLED
    from market_time import is_nifty_btst_window

    if NIFTY_BTST_ENABLED and is_nifty_btst_window():
        from nifty_btst import run_nifty_btst_alert

        run_nifty_btst_alert()
        return 0

    if not is_new_trade_window():
        logger.info("After 3:00 PM IST — BTST at 3:20 PM; summary at 3:30 PM.")
        return 0

    # Session start alert is handled centrally in handle_session_alerts().
    # Keep start timestamp refreshed for delayed boot/session metrics.
    record_trading_started_at()

    watchlist = _get_daily_watchlist()
    if not watchlist:
        logger.warning("Empty watchlist.")
        return 0

    equity_hits, premium_hits = reconcile_all_positions()
    broadcast_lifecycle_updates(equity_hits, premium_hits)

    equity_signals, scan_stats = run_intraday_scan(watchlist)
    signals = list(equity_signals)

    from config import NIFTY_OPTIONS_ENABLED

    if NIFTY_OPTIONS_ENABLED:
        nifty_sig = run_nifty_options_scan()
        if nifty_sig:
            signals.append(nifty_sig)
            scan_stats.nifty_option_sent = True

    try_send_delayed_boot()

    logger.info(
        "Done. Signals sent: %d / %d symbols | raw=%d BUY=%d",
        len(signals),
        len(watchlist),
        scan_stats.raw_setups,
        scan_stats.buy_sent,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
