#!/usr/bin/env python3
"""Run Nifty + Sensex BTST now (use --force outside 3:10-3:29 PM IST window)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even outside the BTST window (still requires market open unless testing)",
    )
    args = parser.parse_args()

    from nifty_btst import run_nifty_btst_alert
    from sensex_btst import run_sensex_btst_alert

    n = run_nifty_btst_alert(force=args.force)
    s = run_sensex_btst_alert(force=args.force)
    print("Nifty:", "sent" if n else "none")
    print("Sensex:", "sent" if s else "none")
    return 0 if n or s else 1


if __name__ == "__main__":
    raise SystemExit(main())
