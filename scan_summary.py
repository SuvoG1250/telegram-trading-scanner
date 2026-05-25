"""Per-scan stats for logs only — Telegram scan pings disabled."""

from __future__ import annotations

from dataclasses import dataclass

from config import SEND_SCAN_SUMMARY
from market_time import now_ist


@dataclass
class ScanStats:
    symbols_scanned: int = 0
    symbols_checked: int = 0
    raw_setups: int = 0
    confirmed_ranked: int = 0
    buy_sent: int = 0
    sell_sent: int = 0
    nifty_option_sent: bool = False


def format_scan_summary(stats: ScanStats) -> str:
    """Log-friendly one line (not sent to Telegram by default)."""
    t = now_ist().strftime("%H:%M IST")
    nifty = "nifty" if stats.nifty_option_sent else "no nifty"
    return (
        f"Scan {t}: {stats.symbols_scanned} symbols | "
        f"{stats.raw_setups} setups | BUY {stats.buy_sent} | {nifty}"
    )


def send_scan_summary(stats: ScanStats) -> bool:
    """Disabled — user wants BUY/SELL/BTST/EOD only, no per-scan Telegram."""
    if SEND_SCAN_SUMMARY:
        import logging

        logging.getLogger("scan_summary").warning(
            "SEND_SCAN_SUMMARY=true ignored; scan Telegram pings are disabled."
        )
    return False
