#!/usr/bin/env python3
"""
GCP automation daemon — no crontab/sudo required.

Single process handles:
  • Telegram commands (/live /strategy /upstox_token) — polls getUpdates here only
  • Mon–Fri 9:10 IST — NSE session (upstox_live_runner, 390 min)
  • Daily 7–8 & 16–22 IST — global session (session_runner, 58 min)

Start once:
  bash scripts/install_gcp_automation.sh
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
import time
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
SCHEDULER_INTERVAL_SEC = 30
TELEGRAM_POLL_INTERVAL_SEC = 1.5

NSE_HOUR, NSE_MIN = 9, 10
GLOBAL_HOURS = (7, 8, 16, 17, 18, 19, 20, 21, 22)


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


def _stop_legacy_listeners() -> None:
    """Remove separate listener subprocesses — scheduler polls Telegram directly."""
    try:
        r = subprocess.run(["pgrep", "-f", "telegram_command_listener.py"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return
        pids = [p.strip() for p in r.stdout.splitlines() if p.strip()]
        if not pids:
            return
        logger.warning("Stopping %s legacy telegram_command_listener process(es): %s", len(pids), pids)
        subprocess.run(["pkill", "-f", "telegram_command_listener.py"], check=False)
        time.sleep(2)
    except (OSError, subprocess.SubprocessError):
        pass


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


def _maybe_daily_strategy_prompt(state: dict) -> None:
    weekday, hour, minute, _ = _now_parts()
    if weekday >= 5:
        return
    from config import SEND_DAILY_STRATEGY_PROMPT

    if not SEND_DAILY_STRATEGY_PROMPT:
        return

    from config import DAILY_STRATEGY_PROMPT_HOUR, DAILY_STRATEGY_PROMPT_MINUTE

    if hour < DAILY_STRATEGY_PROMPT_HOUR or (
        hour == DAILY_STRATEGY_PROMPT_HOUR and minute < DAILY_STRATEGY_PROMPT_MINUTE
    ):
        return

    key = f"daily_strategy_{_today_key()}"
    if state.get(key):
        return

    from daily_strategy_setup import send_daily_strategy_setup

    if send_daily_strategy_setup(force=True):
        state[key] = True
        _save_state(state)


def _maybe_premarket_summary(state: dict) -> None:
    weekday, hour, minute, _ = _now_parts()
    if weekday >= 5:
        return
    if hour < 8 or (hour == 8 and minute < 55):
        return
    if hour > 9 or (hour == 9 and minute > 45):
        return

    key = f"premarket_{_today_key()}"
    if state.get(key):
        return

    from premarket_summary import send_premarket_market_summary

    if send_premarket_market_summary(force_window=True):
        state[key] = True
        _save_state(state)


def _prune_old_state(state: dict) -> dict:
    today = _today_key()
    keep = {k: v for k, v in state.items() if today in k}
    if len(keep) < len(state):
        return keep
    return state


def _scheduler_tick(state: dict) -> dict:
    _maybe_daily_strategy_prompt(state)
    _maybe_premarket_summary(state)
    _maybe_nse_session(state)
    _maybe_global_session(state)
    return state


def main() -> int:
    if not PY.is_file():
        logger.error("Missing venv python: %s", PY)
        return 1
    env_file = ROOT / ".env"
    if not env_file.is_file():
        logger.error("Missing .env in %s", ROOT)
        return 1

    os.environ.setdefault("TZ", "Asia/Kolkata")
    _stop_legacy_listeners()

    from config import telegram_commands_status

    tg_ok, tg_msg = telegram_commands_status()
    if not tg_ok:
        logger.error(
            "Telegram commands NOT active: %s | .env=%s | Fix .env then restart.",
            tg_msg,
            env_file,
        )
    else:
        from telegram_commands import (
            acquire_telegram_poll_ownership,
            poll_telegram_commands,
            release_telegram_poll_ownership,
        )

        if not acquire_telegram_poll_ownership():
            logger.error(
                "Another process is polling Telegram (getUpdates conflict). "
                "Run: pkill -f gcp_scheduler_daemon; pkill -f telegram_command_listener; "
                "bash scripts/install_gcp_automation.sh"
            )
            return 1
        atexit.register(release_telegram_poll_ownership)
        logger.info("Telegram polling embedded in scheduler (single poller, pid=%s).", os.getpid())

    logger.info("GCP scheduler daemon started. Repo: %s", ROOT)

    try:
        subprocess.run([str(PY), "scripts/telegram_delete_webhook.py"], cwd=str(ROOT), check=False, timeout=30)
    except Exception:
        pass

    state = _prune_old_state(_load_state())
    last_scheduler = 0.0

    while True:
        loop_start = time.time()

        if tg_ok:
            try:
                poll_telegram_commands()
            except Exception:
                logger.exception("Telegram poll failed")

        if loop_start - last_scheduler >= SCHEDULER_INTERVAL_SEC:
            try:
                state = _prune_old_state(_load_state())
                state = _scheduler_tick(state)
                _save_state(state)
            except Exception:
                logger.exception("Scheduler tick failed")
            last_scheduler = loop_start

        elapsed = time.time() - loop_start
        time.sleep(max(0.2, TELEGRAM_POLL_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
