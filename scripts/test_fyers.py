#!/usr/bin/env python3
"""Test Fyers App ID + access token (quotes / option chain)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from config import FYERS_ACCESS_TOKEN, FYERS_APP_ID
from fyers_client import (
    _nifty_weekly_expiry_date,
    build_fyers_nifty_option_symbol,
    fetch_nifty_option_quote,
    verify_fyers,
)


def main() -> int:
    print("FYERS_APP_ID:", "set" if FYERS_APP_ID else "MISSING")
    print("FYERS_ACCESS_TOKEN:", "set" if FYERS_ACCESS_TOKEN else "MISSING")
    if not FYERS_APP_ID or not FYERS_ACCESS_TOKEN:
        print("\nAdd FYERS_APP_ID (e.g. MKBRXHLH5S-100) and FYERS_ACCESS_TOKEN from Fyers login flow.")
        print("See: https://myapi.fyers.in/docsv3 (generate authcode -> validate -> access_token)")
        return 1
    if verify_fyers():
        print("OK Fyers index quote / auth")
    else:
        print("FAIL Fyers verify (check token and API permissions: Quotes & Market data)")
        return 1
    exp = _nifty_weekly_expiry_date()
    sym = build_fyers_nifty_option_symbol(exp, 24500, "CE")
    print("sample symbol:", sym)
    q, src = fetch_nifty_option_quote(24500, "CE")
    if q:
        print(f"OK option quote ({src}): LTP={q.last_price} expiry={q.expiry}")
        return 0
    print("WARN: chain/quote empty for 24500 CE (strike may be off-market or parse mismatch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
