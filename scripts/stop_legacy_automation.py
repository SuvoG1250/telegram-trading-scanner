#!/usr/bin/env python3
"""Wrapper: disable legacy cron + cancel GitHub runs + enable single daily job."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    script = Path(__file__).resolve().parent / "setup_cron_job_org.py"
    sys.argv = [str(script), "--reset"]
    runpy.run_path(str(script), run_name="__main__")
