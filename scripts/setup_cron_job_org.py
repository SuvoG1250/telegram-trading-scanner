#!/usr/bin/env python3
"""
Create/update cron-job.org jobs for NSE session + global windows.

Requires in .env or environment:
  CRONJOB_API_KEY  — from https://console.cron-job.org/settings
  GITHUB_PAT       — GitHub token with repo scope (classic PAT)

Usage:
  python scripts/setup_cron_job_org.py --reset
  python scripts/setup_cron_job_org.py --test   # trigger one GitHub scan now
  python scripts/setup_cron_job_org.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        if key:
            os.environ[key] = v.strip().strip('"').strip("'")

CRONJOB_API = "https://api.cron-job.org"
GITHUB_OWNER = "SuvoG1250"
GITHUB_REPO = "telegram-trading-scanner"
WORKFLOW_ID = "278548320"

JOB_TITLE_NSE = "Telegram Trading Bot — NSE session"
JOB_TITLE_GLOBAL = "Telegram Trading Bot — Global window"
KEEP_JOB_TITLES = {JOB_TITLE_NSE, JOB_TITLE_GLOBAL}

DISPATCH_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    f"/actions/workflows/{WORKFLOW_ID}/dispatches"
)


def _cron_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _dispatch_body(*, max_minutes: str) -> str:
    return json.dumps({"ref": "main", "inputs": {"mode": "full_session", "max_minutes": max_minutes}})


def build_nse_job_payload(github_pat: str) -> dict:
    return {
        "job": {
            "title": JOB_TITLE_NSE,
            "enabled": True,
            "saveResponses": False,
            "url": DISPATCH_URL,
            "requestMethod": 1,
            "requestTimeout": 120,
            "schedule": {
                "timezone": "Asia/Kolkata",
                "expiresAt": 0,
                "hours": [9],
                "minutes": [10],
                "mdays": [-1],
                "months": [-1],
                "wdays": [1, 2, 3, 4, 5],
            },
            "extendedData": {
                "headers": {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {github_pat}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                },
                "body": _dispatch_body(max_minutes="390"),
            },
        }
    }


def build_global_job_payload(github_pat: str) -> dict:
    # 7–8 AM pre-market; 16–22 PM post-NSE (no overlap with 9:10–15:40 NSE run).
    global_hours = [7, 8, 16, 17, 18, 19, 20, 21, 22]
    return {
        "job": {
            "title": JOB_TITLE_GLOBAL,
            "enabled": True,
            "saveResponses": False,
            "url": DISPATCH_URL,
            "requestMethod": 1,
            "requestTimeout": 120,
            "schedule": {
                "timezone": "Asia/Kolkata",
                "expiresAt": 0,
                "hours": global_hours,
                "minutes": [0],
                "mdays": [-1],
                "months": [-1],
                "wdays": [-1],
            },
            "extendedData": {
                "headers": {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {github_pat}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                },
                "body": _dispatch_body(max_minutes="58"),
            },
        }
    }


def list_jobs(api_key: str) -> list[dict]:
    r = requests.get(f"{CRONJOB_API}/jobs", headers=_cron_headers(api_key), timeout=30)
    if r.status_code == 401:
        print("cron-job.org rejected the API key (401 Unauthorized).")
        sys.exit(1)
    r.raise_for_status()
    return r.json().get("jobs", [])


def find_job_id(api_key: str, title: str) -> int | None:
    for job in list_jobs(api_key):
        if job.get("title") == title:
            return int(job["jobId"])
    return None


def create_job(api_key: str, payload: dict) -> int:
    r = requests.put(
        f"{CRONJOB_API}/jobs",
        headers=_cron_headers(api_key),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return int(r.json()["jobId"])


def update_job(api_key: str, job_id: int, payload: dict) -> None:
    r = requests.patch(
        f"{CRONJOB_API}/jobs/{job_id}",
        headers=_cron_headers(api_key),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()


def upsert_job(api_key: str, github_pat: str, title: str, payload: dict) -> int:
    existing = find_job_id(api_key, title)
    if existing:
        update_job(api_key, existing, payload)
        print(f"Updated cron-job.org job id={existing} ({title})")
        return existing
    job_id = create_job(api_key, payload)
    print(f"Created cron-job.org job id={job_id} ({title})")
    return job_id


def test_github_dispatch(github_pat: str) -> None:
    r = requests.post(
        DISPATCH_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_pat}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main", "inputs": {"mode": "full_session", "max_minutes": "390"}},
        timeout=30,
    )
    if r.status_code == 204:
        print("GitHub dispatch OK — NSE full_session started (390 min).")
        return
    print(f"GitHub dispatch failed ({r.status_code}): {r.text}")
    sys.exit(1)


def disable_job(api_key: str, job_id: int) -> None:
    r = requests.patch(
        f"{CRONJOB_API}/jobs/{job_id}",
        headers=_cron_headers(api_key),
        json={"job": {"enabled": False}},
        timeout=30,
    )
    r.raise_for_status()


def prune_legacy_cron_jobs(api_key: str) -> int:
    disabled = 0
    for job in list_jobs(api_key):
        title = str(job.get("title") or "")
        if title in KEEP_JOB_TITLES:
            continue
        url = str(job.get("url") or "")
        if "actions/workflows" not in url and str(WORKFLOW_ID) not in url:
            continue
        if not job.get("enabled"):
            continue
        jid = int(job["jobId"])
        disable_job(api_key, jid)
        print(f"Disabled legacy job [{jid}] {title}")
        disabled += 1
    return disabled


def cancel_github_runs() -> int:
    import subprocess

    try:
        out = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--repo",
                f"{GITHUB_OWNER}/{GITHUB_REPO}",
                "--workflow",
                "Auto Trading Bot",
                "--status",
                "in_progress",
                "--json",
                "databaseId",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if out.returncode != 0:
            return 0
        rows = json.loads(out.stdout or "[]")
        for row in rows:
            rid = row.get("databaseId")
            if rid:
                subprocess.run(
                    ["gh", "run", "cancel", str(rid), "--repo", f"{GITHUB_OWNER}/{GITHUB_REPO}"],
                    check=False,
                )
                print(f"Cancelled GitHub run {rid}")
        return len(rows)
    except (OSError, json.JSONDecodeError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup cron-job.org for trading bot")
    parser.add_argument("--test", action="store_true", help="Trigger one GitHub NSE scan now")
    parser.add_argument("--list", action="store_true", help="List cron-job.org jobs")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Disable legacy jobs, cancel stuck runs, enable NSE + global cron jobs",
    )
    args = parser.parse_args()

    github_pat = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN", "")

    def _gh_cli_token() -> str:
        try:
            import subprocess

            out = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if out.returncode == 0:
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        return ""

    if not github_pat or github_pat.startswith("ghp_") and len(github_pat) < 40:
        gh_tok = _gh_cli_token()
        if gh_tok:
            github_pat = gh_tok
            print("Using GitHub token from: gh auth login")

    cron_key = os.environ.get("CRONJOB_API_KEY", "")

    if args.test:
        if not github_pat:
            print("Set GITHUB_PAT in .env (repo scope).")
            return 1
        test_github_dispatch(github_pat)
        return 0

    if args.list:
        if not cron_key:
            print("Set CRONJOB_API_KEY in .env")
            return 1
        for job in list_jobs(cron_key):
            print(f"  [{job.get('jobId')}] {job.get('title')} enabled={job.get('enabled')}")
        return 0

    if not github_pat:
        print("Missing GITHUB_PAT in .env")
        return 1
    if not cron_key:
        print("Missing CRONJOB_API_KEY in .env")
        return 1

    if args.reset:
        n_cancel = cancel_github_runs()
        if n_cancel:
            print(f"Cancelled {n_cancel} in-progress GitHub run(s).")

    n_off = prune_legacy_cron_jobs(cron_key)
    if n_off:
        print(f"Disabled {n_off} legacy cron job(s).")

    upsert_job(cron_key, github_pat, JOB_TITLE_NSE, build_nse_job_payload(github_pat))
    upsert_job(cron_key, github_pat, JOB_TITLE_GLOBAL, build_global_job_payload(github_pat))

    print()
    print("NSE:      Mon–Fri 9:10 IST → full_session 390 min (scan every 3 min)")
    print("Global:   7–8 & 16–22 IST daily → full_session 58 min (BTC/ETH/XAU)")
    print()
    print("Verify: python scripts/setup_cron_job_org.py --test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
