#!/usr/bin/env python3
"""Send today's pre-market summary (optional --force outside 9:10-9:35 IST)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from premarket_summary import send_premarket_market_summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send even outside 9:10-9:35 IST window (if not already sent today)",
    )
    args = parser.parse_args()
    ok = send_premarket_market_summary(force_window=not args.force)
    if ok:
        print("Pre-market summary sent.")
        return 0
    print("Not sent (disabled, already sent today, or outside window).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
