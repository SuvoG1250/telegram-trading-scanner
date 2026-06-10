#!/usr/bin/env python3
"""Intraday NSE scanner — sends Telegram trade signals only (default)."""

from __future__ import annotations

import logging
import os
import sys

import html as html_module

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
from market_time import (
    is_market_open,
    is_new_trade_window,
    is_premarket_summary_window,
    is_premarket_window,
    is_weekday,
    now_ist,
)
from premarket import build_watchlist, format_watchlist_message
from premarket_summary import send_premarket_market_summary
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
from ai_improvements import build_nifty_option_ai_note, should_send_equity_signal
from stock_gemini import (
    apply_focus_score_boost,
    build_alert_ai_note,
    rank_scan_candidates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner")


def run_premarket() -> list[str]:
    """Build today's scan list; send pre-market report when enabled."""
    if send_premarket_market_summary():
        logger.info("Pre-market news summary sent.")
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
                score = apply_focus_score_boost(
                    symbol, equity_candidate_score(telegram_sig)
                )
                raw_candidates.append((symbol, confirmed, telegram_sig, score))
        else:
            confirmed = confirm_signals(raw)
            if confirmed is None:
                continue
            telegram_sig = confirmed.to_telegram_signal()
            strat = " + ".join(confirmed.strategies)
            score = apply_focus_score_boost(
                symbol, equity_candidate_score(telegram_sig)
            )
            raw_candidates.append((symbol, confirmed, telegram_sig, score))

    best_per_symbol: dict[str, tuple[ConfirmedSignal, float, str]] = {}
    for symbol, confirmed, telegram_sig, score in raw_candidates:
        cur = best_per_symbol.get(symbol)
        strat = " + ".join(confirmed.strategies)
        if cur is None or score > cur[1]:
            best_per_symbol[symbol] = (confirmed, score, strat)

    ranked = sorted(best_per_symbol.values(), key=lambda t: -t[1])
    ranked = rank_scan_candidates(ranked)
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
                logger.info(
                    "Skip BUY %s [%s] — scan cap %d or daily left %d.",
                    symbol,
                    strat,
                    MAX_BUY_ALERTS_PER_SCAN,
                    daily_buy_left,
                )
                continue
        else:
            if sell_scan_emitted >= MAX_SELL_ALERTS_PER_SCAN:
                logger.info("Skip SHORT SELL %s [%s] — scan cap reached.", symbol, strat)
                continue

        telegram_sig = confirmed.to_telegram_signal()
        peek = peek_stock_exit_flag(symbol)
        reentry = caption_after_prior_exit(peek, basket="equity")
        if reentry:
            telegram_sig.note = (
                f"{telegram_sig.note}\n{reentry}".strip() if telegram_sig.note else reentry
            )
        lv = telegram_sig.levels
        ok_send, filter_reason = should_send_equity_signal(
            symbol=symbol,
            side=telegram_sig.side,
            strategy=strat,
            entry=float(lv.entry),
            stop_loss=float(lv.stop_loss),
            target=float(lv.primary_target),
            score=score,
            timeframe=telegram_sig.timeframe or "",
        )
        if not ok_send:
            logger.info("Skip %s [%s] — AI filter: %s", symbol, strat, filter_reason)
            continue

        ai_note = build_alert_ai_note(
            symbol=symbol,
            side=telegram_sig.side,
            strategy=strat,
            entry=float(lv.entry),
            stop_loss=float(lv.stop_loss),
            target=float(lv.primary_target),
            timeframe=telegram_sig.timeframe or "",
        )
        if ai_note:
            ai_block = f"🤖 AI: {ai_note}"
            telegram_sig.note = (
                f"{telegram_sig.note}\n{ai_block}".strip() if telegram_sig.note else ai_block
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


def run_index_options_scan(scan_fn, *, index_label: str) -> Signal | None:
    """Supertrend flip on index → Buy CE/PE; blocked until prior premium exits."""
    sig = scan_fn()
    if sig is None:
        return None
    instrument = sig.instrument or "NIFTY_OPTION"
    if premium_position_open(sig.side, instrument):
        logger.info(
            "Skip %s option (%s) — premium plan still awaiting SL/Target.",
            index_label,
            sig.side,
        )
        return None

    peek = peek_option_exit_flag(sig.side, instrument)
    sig.note = caption_after_prior_exit(peek, basket="option")
    lv = sig.levels
    opt_ai = build_nifty_option_ai_note(
        side=sig.side,
        strike=float(sig.strike or 0),
        option_type=str(sig.option_type or "CE"),
        entry=float(lv.entry),
        stop_loss=float(lv.stop_loss),
        target=float(lv.primary_target),
    )
    if opt_ai:
        block = f"🤖 AI: {opt_ai}"
        sig.note = f"{sig.note}\n{block}".strip() if sig.note else block

    ok = send_signal(sig)
    if ok:
        register_premium_open(sig)
        record_trade(sig)
        from upstox_execution import maybe_execute_upstox_trade

        maybe_execute_upstox_trade(sig)
        if peek is not None:
            dismiss_option_exit_flag(sig.side, instrument)
        logger.info("%s option signal sent: %s", index_label, sig.side)
        return sig
    logger.error("Failed to send %s option signal.", index_label)
    return None


def run_nifty_options_scan() -> Signal | None:
    from nifty_options import scan_nifty_supertrend_option

    return run_index_options_scan(scan_nifty_supertrend_option, index_label="NIFTY")


def run_sensex_options_scan() -> Signal | None:
    from sensex_options import scan_sensex_supertrend_option

    return run_index_options_scan(scan_sensex_supertrend_option, index_label="SENSEX")


def run_nifty_ema_macd_options_scan() -> Signal | None:
    from ema_macd_options import scan_nifty_ema_macd_option

    return run_index_options_scan(scan_nifty_ema_macd_option, index_label="NIFTY EMA+MACD")


def run_sensex_ema_macd_options_scan() -> Signal | None:
    from ema_macd_options import scan_sensex_ema_macd_option

    return run_index_options_scan(scan_sensex_ema_macd_option, index_label="SENSEX EMA+MACD")


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
    """Locked daily watchlist is reused — F&O list (~45–120) is not 200+ names."""
    if not SCAN_FULL_UNIVERSE:
        return False
    if is_watchlist_locked() and watchlist:
        return False
    return len(watchlist) < 10


def _get_daily_watchlist() -> list[str]:
    """Always return today's filtered universe during market hours (never stale/empty)."""
    watchlist = load_watchlist()

    if watchlist and is_watchlist_locked() and not _needs_full_universe_refresh(watchlist):
        return watchlist

    logger.info(
        "Building watchlist (cached=%s symbols, locked=%s).",
        len(watchlist),
        is_watchlist_locked(),
    )
    watchlist, ranked = build_watchlist()
    if watchlist:
        from stock_gemini import run_premarket_stock_selection

        if ranked:
            run_premarket_stock_selection(ranked)
        save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
    return watchlist


def main() -> int:
    from global_markets import run_global_assets_alerts

    global_sent = run_global_assets_alerts()
    if global_sent:
        logger.info("Global assets alerts sent: %s", global_sent)

    if not is_weekday():
        logger.info("Weekend — NSE scan skipped.")
        return 0

    if is_premarket_summary_window() and send_premarket_market_summary():
        logger.info("Pre-market news summary sent (early).")

    mode = "signals-only" if SIGNALS_ONLY_TELEGRAM else "verbose"
    from strategies import EQUITY_STRATEGY_LABELS

    strat_mode = (
        f"stocks ({len(EQUITY_STRATEGY_LABELS)} setups, no Chaitu)"
        if SCAN_STRATEGIES in ("all", "stocks", "default")
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

    if is_premarket_window():
        run_premarket()
        return 0

    from config import NIFTY_BTST_ENABLED, STOCK_BTST_ENABLED
    from market_time import is_nifty_btst_window, is_stock_btst_window

    if is_market_open():
        if STOCK_BTST_ENABLED and is_stock_btst_window():
            from stock_btst import run_stock_btst_alerts

            n = run_stock_btst_alerts()
            if n:
                logger.info("Stock BTST alerts sent: %s", n)
            return 0

        if NIFTY_BTST_ENABLED and is_nifty_btst_window():
            from nifty_btst import run_nifty_btst_alert

            run_nifty_btst_alert()
            return 0

    if handle_session_alerts():
        logger.info("Session ended.")
        return 0

    if not is_market_open():
        logger.info("Market closed.")
        return 0

    if not is_new_trade_window():
        logger.info("After 3:00 PM IST — Stock BTST 3:10, Nifty BTST 3:20, summary 3:30 PM.")
        return 0

    # Session start alert is handled centrally in handle_session_alerts().
    # Keep start timestamp refreshed for delayed boot/session metrics.
    record_trading_started_at()

    watchlist = _get_daily_watchlist()
    if not watchlist:
        logger.warning("Empty watchlist.")
        return 0

    equity_hits, premium_hits, _global_hits = reconcile_all_positions()
    broadcast_lifecycle_updates(equity_hits, premium_hits)

    equity_signals, scan_stats = run_intraday_scan(watchlist)
    signals = list(equity_signals)

    from config import EMA_MACD_OPTIONS_ENABLED, NIFTY_OPTIONS_ENABLED, SENSEX_OPTIONS_ENABLED

    if NIFTY_OPTIONS_ENABLED:
        try:
            nifty_sig = run_nifty_options_scan()
        except Exception:
            logger.exception("Nifty options scan failed (continuing equity scan)")
            nifty_sig = None
        if nifty_sig:
            signals.append(nifty_sig)
            scan_stats.nifty_option_sent = True

    if SENSEX_OPTIONS_ENABLED:
        try:
            sensex_sig = run_sensex_options_scan()
        except Exception:
            logger.exception("Sensex options scan failed (continuing equity scan)")
            sensex_sig = None
        if sensex_sig:
            signals.append(sensex_sig)
            scan_stats.sensex_option_sent = True

    if EMA_MACD_OPTIONS_ENABLED:
        if NIFTY_OPTIONS_ENABLED:
            try:
                nifty_ema_sig = run_nifty_ema_macd_options_scan()
            except Exception:
                logger.exception("Nifty EMA+MACD options scan failed (continuing)")
                nifty_ema_sig = None
            if nifty_ema_sig:
                signals.append(nifty_ema_sig)

        if SENSEX_OPTIONS_ENABLED:
            try:
                sensex_ema_sig = run_sensex_ema_macd_options_scan()
            except Exception:
                logger.exception("Sensex EMA+MACD options scan failed (continuing)")
                sensex_ema_sig = None
            if sensex_ema_sig:
                signals.append(sensex_ema_sig)

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
