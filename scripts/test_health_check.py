#!/usr/bin/env python3
"""Print morning health check message (does not send unless --send)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from health_check import build_health_report, send_morning_health_check  # noqa: E402


def main() -> int:
    print(build_health_report())
    if "--send" in sys.argv:
        return 0 if send_morning_health_check() else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
