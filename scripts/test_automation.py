#!/usr/bin/env python3
"""Run local verification + automation health checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], label: str) -> bool:
    print(f"\n=== {label} ===")
    r = subprocess.run(cmd, cwd=ROOT, timeout=120)
    ok = r.returncode == 0
    print("OK" if ok else "FAIL")
    return ok


def main() -> int:
    ok = True
    ok &= _run([sys.executable, "verify_signals.py"], "verify_signals")
    ok &= _run([sys.executable, "scripts/setup_cron_job_org.py", "--list"], "cron-job.org list")
    ok &= _run([sys.executable, "-c", "from dhan_client import dhan_configured; print('dhan', dhan_configured())"], "dhan config")
    ok &= _run(
        [sys.executable, "-c", "from trade_journal import load_today_trades; print(len(load_today_trades()))"],
        "daily summary journal",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
