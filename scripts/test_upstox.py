#!/usr/bin/env python3
"""Test Upstox API key + access token."""

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

from upstox_client import UPSTOX_ACCESS_TOKEN, fetch_expiries, upstox_configured, verify_upstox


def main() -> int:
    print("UPSTOX_ACCESS_TOKEN:", "set" if UPSTOX_ACCESS_TOKEN else "MISSING")
    print("UPSTOX_API_KEY:", "set" if os.environ.get("UPSTOX_API_KEY") else "missing")
    if not upstox_configured():
        print("\nAdd UPSTOX_ACCESS_TOKEN from Upstox Apps -> Analytics -> + Generate Token")
        print("API Key + Secret alone are not enough for market data calls.")
        return 1
    exp = fetch_expiries()
    print("expiries (first 5):", exp[:5] if exp else "none")
    if verify_upstox():
        print("OK Upstox connected")
        return 0
    print("FAIL — check token (use Analytics Generate Token, not only API key)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
