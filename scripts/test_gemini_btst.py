#!/usr/bin/env python3
"""Dry-run Nifty BTST gap probability model."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from index_btst import assess_gap_probability, format_btst_report  # noqa: E402
from nifty_btst import NIFTY_BTST_SPEC  # noqa: E402


def main() -> int:
    a = assess_gap_probability(NIFTY_BTST_SPEC)
    print(format_btst_report(NIFTY_BTST_SPEC, a).replace("<b>", "").replace("</b>", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
