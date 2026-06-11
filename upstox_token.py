"""Upstox access token — env default + Telegram-updated cache in data/."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from config import DATA_DIR, UPSTOX_ACCESS_TOKEN, UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI
from market_time import IST, now_ist

logger = logging.getLogger(__name__)

_TOKEN_FILE = DATA_DIR / "upstox_token.json"
_AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _jwt_exp_ist(token: str) -> datetime | None:
    exp = _jwt_payload(token).get("exp")
    if not exp:
        return None
    try:
        return datetime.fromtimestamp(int(exp), tz=IST)
    except (TypeError, ValueError, OSError):
        return None


def _load_record() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _TOKEN_FILE.exists():
        return {}
    try:
        return json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_record(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_access_token(token: str, *, source: str = "manual") -> dict[str, Any]:
    token = (token or "").strip()
    exp = _jwt_exp_ist(token)
    record = {
        "access_token": token,
        "source": source,
        "updated_at": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
        "expires_at": exp.strftime("%Y-%m-%d %H:%M:%S IST") if exp else "",
    }
    _save_record(record)
    logger.info("Upstox access token saved (source=%s, expires=%s)", source, record.get("expires_at"))
    return record


def get_access_token() -> str:
    """Cached Telegram token overrides env (daily refresh without editing GitHub secrets)."""
    cached = (_load_record().get("access_token") or "").strip()
    if cached:
        return cached
    return (UPSTOX_ACCESS_TOKEN or "").strip()


def token_expiry_ist() -> datetime | None:
    token = get_access_token()
    if not token:
        return None
    cached_exp = (_load_record().get("expires_at") or "").strip()
    if cached_exp:
        try:
            return datetime.strptime(cached_exp, "%Y-%m-%d %H:%M:%S IST").replace(tzinfo=IST)
        except ValueError:
            pass
    return _jwt_exp_ist(token)


def token_is_expired() -> bool:
    exp = token_expiry_ist()
    if exp is None:
        return False
    return now_ist() >= exp


def token_status_line() -> str:
    token = get_access_token()
    if not token:
        return "❌ No Upstox token — send <code>/upstox_token YOUR_TOKEN</code>"
    if token_is_expired():
        return "❌ Upstox token <b>expired</b> — refresh with <code>/upstox_token</code> or <code>/upstox_login</code>"
    exp = token_expiry_ist()
    src = _load_record().get("source") or ("env" if UPSTOX_ACCESS_TOKEN else "unknown")
    if exp:
        return f"✅ Upstox token valid until <b>{exp.strftime('%d %b %H:%M IST')}</b> ({src})"
    return f"✅ Upstox token set ({src})"


def build_auth_url() -> tuple[str | None, str]:
    if not UPSTOX_API_KEY:
        return None, "UPSTOX_API_KEY not configured"
    if not UPSTOX_REDIRECT_URI:
        return None, "UPSTOX_REDIRECT_URI not set — copy Redirect URL from your Upstox app into GitHub secrets / .env"
    q = urlencode(
        {
            "response_type": "code",
            "client_id": UPSTOX_API_KEY,
            "redirect_uri": UPSTOX_REDIRECT_URI,
            "state": "telegram_bot",
        }
    )
    return f"{_AUTH_URL}?{q}", ""


def parse_auth_code(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "http" not in raw:
        return raw
    qs = parse_qs(urlparse(raw).query)
    for key in ("code",):
        if key in qs and qs[key]:
            return qs[key][0].strip()
    return ""


def exchange_auth_code(code: str) -> tuple[str | None, str]:
    code = parse_auth_code(code) or (code or "").strip()
    if not code:
        return None, "Missing authorization code"
    if not UPSTOX_API_KEY or not UPSTOX_API_SECRET:
        return None, "UPSTOX_API_KEY / UPSTOX_API_SECRET not configured"
    if not UPSTOX_REDIRECT_URI:
        return None, "UPSTOX_REDIRECT_URI not configured"

    try:
        resp = requests.post(
            _TOKEN_URL,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            data={
                "code": code,
                "client_id": UPSTOX_API_KEY,
                "client_secret": UPSTOX_API_SECRET,
                "redirect_uri": UPSTOX_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        body = resp.json() if resp.text else {}
        if not resp.ok:
            msg = body.get("message") or body.get("errors") or resp.text[:300]
            return None, str(msg)
        token = str(body.get("access_token") or "")
        if not token:
            return None, "No access_token in Upstox response"
        save_access_token(token, source="oauth")
        return token, "OK"
    except requests.RequestException as exc:
        return None, str(exc)
