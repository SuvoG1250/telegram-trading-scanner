#!/usr/bin/env python3
"""Push .env secrets to GitHub Actions (repo secrets)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_env = ROOT / ".env"

SECRET_KEYS = [
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_GROUP_CHAT_ID",
    "DHAN_ACCESS_TOKEN",
    "DHAN_CLIENT_ID",
    "UPSTOX_ACCESS_TOKEN",
    "UPSTOX_API_KEY",
    "UPSTOX_API_SECRET",
    "UPSTOX_REDIRECT_URI",
    "FYERS_APP_ID",
    "FYERS_SECRET_KEY",
    "FYERS_ACCESS_TOKEN",
]


def _load_env() -> None:
    if not _env.exists():
        return
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _gh_available() -> bool:
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def set_secret(name: str, value: str) -> bool:
    if not value:
        print(f"  skip {name} (empty)")
        return False
    proc = subprocess.run(
        ["gh", "secret", "set", name, "--body", value],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        print(f"  OK {name}")
        return True
    print(f"  FAIL {name}: {proc.stderr.strip() or proc.stdout.strip()}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync .env secrets to GitHub Actions")
    parser.add_argument("--dry-run", action="store_true", help="List secrets only")
    args = parser.parse_args()

    _load_env()
    if not _gh_available():
        print("GitHub CLI not authenticated. Run: gh auth login")
        return 1

    print("GitHub repo secrets:")
    ok = 0
    for key in SECRET_KEYS:
        val = os.environ.get(key, "")
        if args.dry_run:
            print(f"  {key}: {'set' if val else 'missing'}")
            continue
        if set_secret(key, val):
            ok += 1

    if args.dry_run:
        return 0
    print(f"\nSynced {ok}/{len(SECRET_KEYS)} secrets.")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
