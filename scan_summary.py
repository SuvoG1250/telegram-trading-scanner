"""Per-scan Telegram summary (symbols scanned, setups, alerts sent)."""

from __future__ import annotations

from dataclasses import dataclass

from config import SEND_SCAN_SUMMARY
from market_time import now_ist
from telegram_client import send_plain


@dataclass
class ScanStats:
    symbols_scanned: int = 0
    symbols_checked: int = 0
    single_strategy_only: int = 0
    raw_setups: int = 0
    confirmed_ranked: int = 0
    buy_sent: int = 0
    sell_sent: int = 0
    nifty_option_sent: bool = False


def format_scan_summary(stats: ScanStats) -> str:
    t = now_ist().strftime("%H:%M IST")
    nifty = "1 Nifty option" if stats.nifty_option_sent else "no Nifty option"
    return (
        f"📊 <b>Scan {t}</b>: {stats.symbols_scanned} symbols | "
        f"{stats.raw_setups} raw (2-strategy) | "
        f"<b>{stats.buy_sent} BUY</b> sent"
        f" | {nifty}"
        + (
            f" | {stats.single_strategy_only} had 1/2 strategy only"
            if stats.single_strategy_only
            else ""
        )
    )


def send_scan_summary(stats: ScanStats) -> bool:
    if not SEND_SCAN_SUMMARY:
        return False
    return send_plain(format_scan_summary(stats), html_mode=True)
