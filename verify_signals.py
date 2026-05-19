#!/usr/bin/env python3
"""Verify playbook signal builder and strategy registration."""

from __future__ import annotations

import sys

from trade_filters import min_equity_target_profit_pct
from signal_builder import (
    entry_long,
    playbook_entry_long,
    playbook_entry_short,
)
import pandas as pd

from chaitu50c import ChaituParams, replay_last_bar_signal
from config import SCAN_STRATEGIES
from indicators import supertrend_flip_pine
from ema_crossover import add_emas, crossover_signal
from risk import levels_ema_crossover
from strategies import STRATEGY_NAMES, STRATEGY_SCANNERS


def test_playbook_builder() -> bool:
    ok = True
    sig = playbook_entry_long("RELIANCE", "Test", 2500.0, 2470.0, note="Unit test")
    if not sig or sig.levels.entry != 2500.0:
        print("FAIL playbook_entry_long")
        ok = False
    if sig and sig.levels.risk_pct > 0.61:
        print("FAIL SL should be capped near 0.6%")
        ok = False
    sig2 = playbook_entry_short("TCS", "Test", 4000.0, 4020.0)
    if not sig2 or sig2.levels.primary_target >= 4000.0:
        print("FAIL playbook_entry_short")
        ok = False
    bad = entry_long("X", "Test", 100.0, 105.0)
    if bad is not None:
        print("FAIL should reject invalid long SL")
        ok = False
    wide = entry_long("X", "Test", 1000.0, 994.5, rr1=2.0, rr2=2.0, best_rr=2.0)
    if wide is None or wide.levels.target_profit_pct("BUY") < min_equity_target_profit_pct():
        print("FAIL legacy entry should pass profit rule when RR is wide enough")
        ok = False
    too_wide = entry_long("X", "Test", 1000.0, 990.0, rr1=2.0, rr2=2.0, best_rr=2.0)
    if too_wide is not None:
        print("FAIL should reject when SL risk > 0.6% of price")
        ok = False
    return ok


def test_chaitu50c_buy1() -> bool:
    ist = "Asia/Kolkata"
    idx = pd.DatetimeIndex(
        [
            "2026-05-18 10:00:00",
            "2026-05-18 10:05:00",
            "2026-05-18 10:10:00",
        ],
        tz=ist,
    )
    session = pd.DataFrame(
        {
            "Open": [100.0, 100.0, 99.0],
            "High": [101.0, 101.0, 102.0],
            "Low": [99.0, 98.0, 99.0],
            "Close": [100.0, 99.0, 102.0],
            "Volume": [1000, 1000, 1000],
        },
        index=idx,
    )
    fire = replay_last_bar_signal(session, ChaituParams(enhanced_mode=True))
    if fire is None or fire.side != "BUY":
        print("FAIL chaitu50c expected BUY on last bar")
        return False
    if fire.stop_level > fire.entry:
        print("FAIL chaitu50c BUY stop should be below entry")
        return False
    return True


def test_supertrend_flip() -> bool:
    st = pd.DataFrame({"direction": [1.0, 1.0, -1.0]})
    if supertrend_flip_pine(st) != "CALL":
        print("FAIL supertrend flip should be CALL")
        return False
    st2 = pd.DataFrame({"direction": [-1.0, -1.0, 1.0]})
    if supertrend_flip_pine(st2) != "PUT":
        print("FAIL supertrend flip should be PUT")
        return False
    return True


def test_ema_crossover() -> bool:
    ist = "Asia/Kolkata"
    idx = pd.DatetimeIndex(
        [f"2026-05-19 10:{m:02d}:00" for m in range(20)],
        tz=ist,
    )
    close = [100 + i * 0.1 for i in range(20)]
    session = pd.DataFrame(
        {
            "Open": close,
            "High": [c + 0.5 for c in close],
            "Low": [c - 0.5 for c in close],
            "Close": close,
            "Volume": [1_000_000] * 20,
        },
        index=idx,
    )
    session.iloc[-2, session.columns.get_loc("Close")] = 98.0
    session.iloc[-1, session.columns.get_loc("Close")] = 102.0
    df = add_emas(session)
    df.iloc[-2, df.columns.get_loc("EMA_Fast")] = 99.0
    df.iloc[-2, df.columns.get_loc("EMA_Slow")] = 100.0
    df.iloc[-1, df.columns.get_loc("EMA_Fast")] = 101.0
    df.iloc[-1, df.columns.get_loc("EMA_Slow")] = 100.0
    if crossover_signal(df) != "BUY":
        print("FAIL ema crossover BUY")
        return False
    lv = levels_ema_crossover(102.0, 98.5, "BUY")
    if lv is None or lv.target_profit_pct("BUY") < 2.0:
        print("FAIL ema levels min 2% profit")
        return False
    if lv.risk_pct > 0.55:
        print("FAIL ema SL should be low risk <=0.5%")
        return False
    return True


def test_exit490_supertrend() -> bool:
    from indicators import compute_supertrend_exit490, supertrend_flip_pine

    ist = "Asia/Kolkata"
    idx = pd.date_range("2026-01-02 09:15", periods=40, freq="5min", tz=ist)
    base = 24000.0
    noise = [(-1) ** (i // 3) * (i % 5) for i in range(40)]
    close = [base + n for n in noise]
    df = pd.DataFrame(
        {
            "Open": close,
            "High": [c + 8 for c in close],
            "Low": [c - 8 for c in close],
            "Close": close,
            "Volume": [1_000_000] * 40,
        },
        index=idx,
    )
    st = compute_supertrend_exit490(df, bars_back=1, mult=3.0)
    if len(st) != 40 or "direction" not in st.columns:
        print("FAIL exit490 supertrend shape")
        return False
    mapped = st.assign(direction=-st["direction"])
    supertrend_flip_pine(mapped)
    return True


def test_strategies_import() -> bool:
    print(f"Playbook modules ({len(STRATEGY_SCANNERS)}):")
    for name in STRATEGY_NAMES:
        print(f"  OK {name}")
    expected = {
        "all": 4,
        "both": 2,
        "ema": 1,
        "chaitu": 1,
    }.get(SCAN_STRATEGIES, 1)
    return len(STRATEGY_SCANNERS) == expected


def main() -> int:
    print("=== Master playbook verification ===\n")
    if not test_playbook_builder():
        return 1
    print("OK Playbook signal builder\n")
    if not test_chaitu50c_buy1():
        return 1
    print("OK Chaitu50c Pine replay (buy1)\n")
    if not test_supertrend_flip():
        return 1
    print("OK Nifty Supertrend flip (CALL/PUT)\n")
    if not test_exit490_supertrend():
        return 1
    print("OK exit490 SuperTrend engine\n")
    if not test_ema_crossover():
        return 1
    print("OK EMA 9/15 crossover + 2-3% target\n")
    if not test_strategies_import():
        return 1
    print("\nOK Strategy scanners registered")
    print("\nTelegram (signals-only): BUY/SELL + Entry + SL + Target + time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
