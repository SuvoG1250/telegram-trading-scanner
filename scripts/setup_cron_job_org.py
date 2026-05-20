#!/usr/bin/env python3
"""
Create/update cron-job.org job to trigger GitHub scanner every 5 min (Mon-Fri, NSE hours).

Requires in .env or environment:
  CRONJOB_API_KEY  — from https://console.cron-job.org/settings
  GITHUB_PAT       — GitHub token with repo scope (classic PAT)

Usage:
  python scripts/setup_cron_job_org.py
  python scripts/setup_cron_job_org.py --test   # trigger one GitHub scan now
  python scripts/setup_cron_job_org.py --list    # list existing cron-job.org jobs
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
JOB_TITLE = "Telegram Trading Bot — NSE every 5 min"

DISPATCH_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    f"/actions/workflows/{WORKFLOW_ID}/dispatches"
)


def _cron_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def build_job_payload(github_pat: str) -> dict:
    return {
        "job": {
            "title": JOB_TITLE,
            "enabled": True,
            "saveResponses": False,
            "url": DISPATCH_URL,
            "requestMethod": 1,
            "requestTimeout": 120,
            "schedule": {
                "timezone": "Asia/Kolkata",
                "expiresAt": 0,
                "hours": [9, 10, 11, 12, 13, 14, 15],
                "minutes": [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57],
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
                "body": json.dumps({"ref": "main", "inputs": {"mode": "scan_once"}}),
            },
        }
    }


def list_jobs(api_key: str) -> list[dict]:
    r = requests.get(f"{CRONJOB_API}/jobs", headers=_cron_headers(api_key), timeout=30)
    if r.status_code == 401:
        print("cron-job.org rejected the API key (401 Unauthorized).")
        print("Use the API key from https://console.cron-job.org/settings")
        print("(not a job ID or account UUID — it is a long random string).")
        sys.exit(1)
    r.raise_for_status()
    return r.json().get("jobs", [])


def find_job_id(api_key: str) -> int | None:
    for job in list_jobs(api_key):
        if job.get("title") == JOB_TITLE:
            return int(job["jobId"])
    return None


def create_job(api_key: str, github_pat: str) -> int:
    r = requests.put(
        f"{CRONJOB_API}/jobs",
        headers=_cron_headers(api_key),
        json=build_job_payload(github_pat),
        timeout=30,
    )
    r.raise_for_status()
    return int(r.json()["jobId"])


def update_job(api_key: str, job_id: int, github_pat: str) -> None:
    r = requests.patch(
        f"{CRONJOB_API}/jobs/{job_id}",
        headers=_cron_headers(api_key),
        json=build_job_payload(github_pat),
        timeout=30,
    )
    r.raise_for_status()


def test_github_dispatch(github_pat: str) -> None:
    r = requests.post(
        DISPATCH_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_pat}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main", "inputs": {"mode": "scan_once"}},
        timeout=30,
    )
    if r.status_code == 204:
        print("GitHub dispatch OK — check Actions and Telegram in ~1 min.")
        return
    print(f"GitHub dispatch failed ({r.status_code}): {r.text}")
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup cron-job.org for trading bot")
    parser.add_argument("--test", action="store_true", help="Trigger one GitHub scan now")
    parser.add_argument("--list", action="store_true", help="List cron-job.org jobs")
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
    elif github_pat:
        test = requests.post(
            DISPATCH_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {github_pat}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": "main", "inputs": {"mode": "scan_once"}},
            timeout=15,
        )
        if test.status_code == 401:
            gh_tok = _gh_cli_token()
            if gh_tok:
                github_pat = gh_tok
                print("GITHUB_PAT invalid — using gh auth login instead")
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
        print("Create: https://github.com/settings/tokens → classic → repo scope")
        return 1
    if not cron_key:
        print("Missing CRONJOB_API_KEY in .env")
        print("Create: https://console.cron-job.org/settings -> API key")
        return 1

    existing = find_job_id(cron_key)
    if existing:
        update_job(cron_key, existing, github_pat)
        print(f"Updated cron-job.org job id={existing}")
    else:
        job_id = create_job(cron_key, github_pat)
        print(f"Created cron-job.org job id={job_id}")

    print()
    print("Schedule: every 5 min, Mon-Fri, 9:00-15:55 IST (Asia/Kolkata)")
    print("Each run triggers GitHub -> scanner -> Telegram signal or 'no signal'")
    print()
    print("Verify: python scripts/setup_cron_job_org.py --test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
