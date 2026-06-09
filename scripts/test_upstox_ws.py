#!/usr/bin/env python3
"""Test Upstox WebSocket LTP (needs UPSTOX_ACCESS_TOKEN)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upstox_api import UPSTOX_NIFTY_INSTRUMENT_KEY, fetch_nifty_option_quote, upstox_configured
from upstox_websocket import get_ws_ltp, start_upstox_feed, subscribe_instruments


def main() -> int:
    if not upstox_configured():
        print("Set UPSTOX_ACCESS_TOKEN in .env")
        return 1
    q = fetch_nifty_option_quote(24500, "CE")
    if not q or not q.instrument_key:
        print("Could not resolve ATM Nifty option instrument_key")
        return 1
    print("instrument_key:", q.instrument_key, "REST LTP:", q.last_price)
    start_upstox_feed()
    subscribe_instruments([q.instrument_key, UPSTOX_NIFTY_INSTRUMENT_KEY])
    for i in range(12):
        time.sleep(2)
        ws = get_ws_ltp(q.instrument_key)
        print(f"  tick {i + 1}: WS LTP = {ws}")
        if ws:
            print("OK WebSocket LTP received")
            return 0
    print("No WS ticks in 24s — check token / market hours")
    return 1


if __name__ == "__main__":
    sys.exit(main())
