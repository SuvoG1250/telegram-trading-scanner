#!/usr/bin/env python3
"""Rebuild F&O cache + filtered intraday watchlist (run before deploy)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import LOCK_WATCHLIST_FOR_DAY, USE_TRADE_FILTERS
from premarket import build_watchlist
from state import save_watchlist
from stocks import load_fno_symbols


def main() -> int:
    wl_path = ROOT / "data" / "watchlist.json"
    if wl_path.exists():
        wl_path.unlink()
        print("Removed old watchlist.json")

    fno = load_fno_symbols(refresh=True)
    print(f"F&O symbols: {len(fno)}")

    watchlist, ranked = build_watchlist()
    if not watchlist:
        print("ERROR: empty watchlist")
        return 1

    save_watchlist(watchlist, locked=LOCK_WATCHLIST_FOR_DAY)
    print(f"Saved watchlist: {len(watchlist)} symbols (filters={USE_TRADE_FILTERS})")
    print(f"Playbook highlights: {len(ranked)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
