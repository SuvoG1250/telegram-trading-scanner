#!/usr/bin/env python3
"""Verify all strategies load and signal builder validates correctly."""

from __future__ import annotations

import sys

from config import MIN_TARGET_PROFIT_PCT
from risk import levels_for_long, levels_for_short
from signal_builder import entry_long, entry_short, validate_plan, TradePlan
from strategies import STRATEGY_NAMES, STRATEGY_SCANNERS


def test_builder() -> bool:
    ok = True
    sig = entry_long("RELIANCE", "Test", 2500.0, 2480.0, note="Unit test")
    if not sig or sig.levels.entry != 2500.0:
        print("FAIL entry_long")
        ok = False
    sig2 = entry_short("TCS", "Test", 4000.0, 4020.0)
    if not sig2 or sig2.levels.primary_target >= 4000.0:
        print("FAIL entry_short")
        ok = False
    bad = entry_long("X", "Test", 100.0, 105.0)
    if bad is not None:
        print("FAIL should reject invalid long SL")
        ok = False
    # Target profit < 1% should reject (tight SL → small R:R targets)
    tight = entry_long("X", "Test", 1000.0, 999.0, rr1=0.5, rr2=0.5, best_rr=0.5)
    if tight is not None:
        print("FAIL should reject when target profit < MIN_TARGET_PROFIT_PCT")
        ok = False
    wide = entry_long("X", "Test", 1000.0, 980.0, rr1=1.5, rr2=2.0, best_rr=2.0)
    if wide is None or wide.levels.target_profit_pct("BUY") < MIN_TARGET_PROFIT_PCT:
        print("FAIL should accept when target profit >= MIN_TARGET_PROFIT_PCT")
        ok = False
    return ok


def test_strategies_import() -> bool:
    print(f"Strategies loaded ({len(STRATEGY_SCANNERS)}):")
    for name in STRATEGY_NAMES:
        print(f"  OK {name}")
    return len(STRATEGY_SCANNERS) == 6


def main() -> int:
    print("=== Signal system verification ===\n")
    if not test_builder():
        return 1
    print("OK Signal builder & validation\n")
    if not test_strategies_import():
        return 1
    print("\nOK All strategies registered")
    print("\nEach live alert includes:")
    print("  • Stock Name (NSE)")
    print("  • Entry Price")
    print("  • Stop Loss")
    print("  • Best Target (primary R:R)")
    print("  • T1 / T2 + Trade Plan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
