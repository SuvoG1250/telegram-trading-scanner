#!/usr/bin/env python3
"""
GCP automation daemon — no crontab/sudo required.

Replaces cron-job.org schedule on a VPS:
  • Keep Telegram command listener alive 24/7
  • Mon–Fri 9:10 IST — NSE session (upstox_live_runner, 390 min)
  • Daily 7–8 & 16–22 IST — global session (session_runner, 58 min)

Start once:
  nohup ./venv/bin/python scripts/gcp_scheduler_daemon.py >> ~/tradingbot-logs/scheduler.log 2>&1 &
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gcp_scheduler")

STATE_FILE = ROOT / "data" / "gcp_scheduler_state.json"
LOG_DIR = Path.home() / "tradingbot-logs"
PY = ROOT / "venv" / "bin" / "python"
POLL_SEC = 30

NSE_HOUR, NSE_MIN = 9, 10
GLOBAL_HOURS = (7, 8, 16, 17, 18, 19, 20, 21, 22)
CMD_RESTART_HOUR, CMD_RESTART_MIN = 6, 55


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _today_key() -> str:
    from market_time import now_ist

    return now_ist().strftime("%Y-%m-%d")


def _now_parts() -> tuple[int, int, int, int]:
    from market_time import now_ist

    dt = now_ist()
    return dt.weekday(), dt.hour, dt.minute, dt.day


def _pgrep(pattern: str) -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _start_bg(args: list[str], log_name: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
    env = os.environ.copy()
    env["TZ"] = "Asia/Kolkata"
    with log_path.open("a", encoding="utf-8") as logf:
        subprocess.Popen(
            args,
            cwd=str(ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    logger.info("Started: %s -> %s", " ".join(args), log_path)


def _ensure_commands_listener(state: dict) -> None:
    weekday, hour, minute, _ = _now_parts()
    day = _today_key()
    restart_key = f"cmd_restart_{day}"
    due_restart = hour == CMD_RESTART_HOUR and minute >= CMD_RESTART_MIN
    if due_restart and state.get(restart_key):
        due_restart = False

    running = _pgrep("telegram_command_listener.py")
    if running and not due_restart:
        return

    if running and due_restart:
        subprocess.run(["pkill", "-f", "telegram_command_listener.py"], check=False)
        time.sleep(1)

    _start_bg(
        [str(PY), "scripts/telegram_command_listener.py", "--seconds", "86400", "--interval", "1.5"],
        "commands.log",
    )
    state[restart_key] = True
    _save_state(state)


def _maybe_nse_session(state: dict) -> None:
    weekday, hour, minute, _ = _now_parts()
    if weekday >= 5:
        return
    if hour < NSE_HOUR or (hour == NSE_HOUR and minute < NSE_MIN):
        return

    key = f"nse_{_today_key()}"
    if state.get(key):
        return
    if _pgrep("upstox_live_runner.py"):
        state[key] = True
        _save_state(state)
        return

    _start_bg([str(PY), "upstox_live_runner.py", "--max-minutes", "390"], "nse-session.log")
    state[key] = True
    _save_state(state)


def _maybe_global_session(state: dict) -> None:
    weekday, hour, minute, day = _now_parts()
    if hour not in GLOBAL_HOURS or minute > 5:
        return

    key = f"global_{_today_key()}_{hour:02d}"
    if state.get(key):
        return
    if _pgrep("session_runner.py"):
        return

    _start_bg([str(PY), "session_runner.py", "--max-minutes", "58"], "global-session.log")
    state[key] = True
    _save_state(state)


def _prune_old_state(state: dict) -> dict:
    today = _today_key()
    keep = {k: v for k, v in state.items() if today in k or k.startswith("cmd_restart_")}
    if len(keep) < len(state):
        return keep
    return state


def main() -> int:
    if not PY.is_file():
        logger.error("Missing venv python: %s", PY)
        return 1
    if not (ROOT / ".env").is_file():
        logger.error("Missing .env in %s", ROOT)
        return 1

    os.environ.setdefault("TZ", "Asia/Kolkata")
    logger.info("GCP scheduler daemon started (no crontab). Repo: %s", ROOT)

    try:
        subprocess.run([str(PY), "scripts/telegram_delete_webhook.py"], cwd=str(ROOT), check=False, timeout=30)
    except Exception:
        pass

    while True:
        try:
            state = _prune_old_state(_load_state())
            _ensure_commands_listener(state)
            _maybe_nse_session(state)
            _maybe_global_session(state)
        except Exception:
            logger.exception("Scheduler tick failed")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
