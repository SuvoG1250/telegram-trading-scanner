#!/usr/bin/env python3
"""Upstox OAuth login — browser → paste code → save trading access token."""

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


def main() -> int:
    from upstox_token import build_auth_url, exchange_auth_code, token_status_line
    from upstox_api import verify_upstox_trading

    url, err = build_auth_url()
    if not url:
        print(err)
        print("Set UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI in .env")
        return 1

    print("Open this URL, log in, approve:")
    print(url)
    print()
    raw = input("Paste redirect URL (or code only): ").strip()
    token, err = exchange_auth_code(raw)
    if not token:
        print("Failed:", err)
        return 1

    ok, msg = verify_upstox_trading()
    print(token_status_line())
    print("Trading check:", msg if ok else f"WARN: {msg}")
    print("Token cached in data/upstox_token.json")
    print("Optional: python scripts/setup_github_secrets.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
