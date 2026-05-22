#!/usr/bin/env python3
"""Download NSE EQ list and cache symbols in the configured price band (default ₹100–₹1000)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import MAX_STOCK_PRICE, MIN_STOCK_PRICE, SCAN_UNIVERSE_MODE
from stocks import load_fno_symbols, load_nse_equity_symbols, refresh_nse_price_band_universe


def main() -> int:
    if SCAN_UNIVERSE_MODE not in ("nse_price_band", "nse_all", "all_nse", "price_band"):
        print(f"SCAN_UNIVERSE_MODE={SCAN_UNIVERSE_MODE} — price-band refresh skipped.")
        return 0

    eq = load_nse_equity_symbols()
    print(f"NSE EQ symbols loaded: {len(eq)}")

    band = refresh_nse_price_band_universe(force=True)
    print(f"Price band Rs {MIN_STOCK_PRICE:.0f}-{MAX_STOCK_PRICE:.0f}: {len(band)} symbols")

    fno = load_fno_symbols()
    overlap = sorted(set(band) & fno)
    print(f"F&O overlap (trade filters may use): {len(overlap)}")
    return 0 if band else 1


if __name__ == "__main__":
    sys.exit(main())
