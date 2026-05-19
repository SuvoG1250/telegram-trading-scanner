#!/usr/bin/env python3
"""Test Dhan token against production and sandbox APIs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

tok = os.environ.get("DHAN_ACCESS_TOKEN", "")
cid = os.environ.get("DHAN_CLIENT_ID", "")
if not tok or not cid:
    print("Missing DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID in .env")
    sys.exit(1)

headers = {
    "access-token": tok,
    "client-id": cid,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

for base in ("https://api.dhan.co/v2", "https://sandbox.dhan.co/v2"):
    print(f"\n--- {base} ---")
    try:
        r = requests.get(f"{base}/profile", headers=headers, timeout=20)
        print("profile:", r.status_code, r.text[:250])
        r2 = requests.post(
            f"{base}/optionchain/expirylist",
            headers=headers,
            json={"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"},
            timeout=20,
        )
        print("expirylist:", r2.status_code, r2.text[:250])
    except requests.RequestException as e:
        print("error:", e)

print("\n--- Summary ---")
print("Sandbox token (developer.dhanhq.co) -> use DHAN_SANDBOX=true")
print("Live option premium -> web.dhan.co token + Data API Active + DHAN_SANDBOX=false")
