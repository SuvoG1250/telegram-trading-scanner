#!/usr/bin/env python3
"""Validate .env on GCP — run after uploading .env manually."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    env_path = ROOT / ".env"
    alt_env = ROOT / "env"

    print(f"Repo: {ROOT}")
    print(f".env path: {env_path}")
    print(f".env exists: {env_path.is_file()}")
    if env_path.is_file():
        print(f".env size: {env_path.stat().st_size} bytes")
        print(f".env lines: {len(env_path.read_text(encoding='utf-8-sig', errors='replace').splitlines())}")
    elif alt_env.is_file():
        print(f"WARNING: found 'env' without dot — rename: mv env .env")
        return 1

    from env_loader import load_dotenv
    from config import (
        TELEGRAM_CHAT_ID,
        TELEGRAM_COMMANDS_ENABLED,
        TELEGRAM_POLL_IN_SESSION,
        TELEGRAM_TOKEN,
        telegram_chat_ids,
        telegram_commands_status,
    )

    loaded = load_dotenv(env_path)
    ok, msg = telegram_commands_status()

    print(f"Keys loaded: {len(loaded)}")
    print(f"TELEGRAM_COMMANDS_ENABLED: {TELEGRAM_COMMANDS_ENABLED}")
    print(f"TELEGRAM_POLL_IN_SESSION: {TELEGRAM_POLL_IN_SESSION}")
    print(f"TELEGRAM_TOKEN: {'SET (' + str(len(TELEGRAM_TOKEN)) + ' chars)' if TELEGRAM_TOKEN else 'MISSING'}")
    print(f"TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID or 'MISSING'}")
    print(f"telegram_chat_ids(): {telegram_chat_ids()}")
    print(f"telegram_commands_status: {ok} — {msg}")

    if not ok:
        print("\nFIX:")
        print("  1) Upload .env to exactly:", env_path)
        print("  2) First line must be: TELEGRAM_TOKEN=123456:ABC...")
        print("  3) Not .env.txt — must be hidden file .env")
        print("  4) Then: bash scripts/install_gcp_automation.sh")
        return 1

    print("\n.env OK — restart bot:")
    print("  bash scripts/install_gcp_automation.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
