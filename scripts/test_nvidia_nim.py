#!/usr/bin/env python3
"""Verify NVIDIA NIM API (needs NVIDIA_NIM_API_KEY in .env)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import NVIDIA_NIM_API_KEY, NVIDIA_NIM_MODEL  # noqa: E402
from nvidia_nim_client import nvidia_nim_available, nvidia_nim_generate  # noqa: E402


def main() -> int:
    if not nvidia_nim_available():
        print("Set NVIDIA_NIM_API_KEY in .env (from build.nvidia.com)")
        return 1
    print("Model:", NVIDIA_NIM_MODEL)
    text = nvidia_nim_generate("Reply with exactly: NVIDIA_OK", max_tokens=20, temperature=0.0)
    print("Response:", text or "(empty)")
    return 0 if text else 1


if __name__ == "__main__":
    raise SystemExit(main())
