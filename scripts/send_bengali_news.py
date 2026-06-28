#!/usr/bin/env python3
"""Send or print Bengali 24h market news analysis."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_news_analyst import format_bengali_market_news_analysis, send_bengali_market_news_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="Bengali 24h market news analyst")
    parser.add_argument("--print-only", action="store_true", help="Print to stdout instead of Telegram")
    parser.add_argument("--force", action="store_true", help="Send even if already sent today")
    args = parser.parse_args()

    if args.print_only:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print(format_bengali_market_news_analysis())
        return 0

    ok = send_bengali_market_news_analysis(force=args.force)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
