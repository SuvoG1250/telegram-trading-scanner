#!/usr/bin/env python3
"""
One-time Fyers v3 login: print auth URL → you log in → paste ?code=... → prints access_token for .env

Requires in .env:
  FYERS_APP_ID
  FYERS_SECRET_KEY
  FYERS_REDIRECT_URI  (optional — default matches Fyers' common app redirect; MUST equal My API → app → Redirect URL exactly)

If you see "redirectUrl mismatch" in the browser, copy the Redirect URL from the Fyers app page into FYERS_REDIRECT_URI and run this script again.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


FYERS_API = "https://api-t1.fyers.in/api/v3"


def _app_id_hash(client_id: str, secret_key: str) -> str:
    return hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()


def main() -> int:
    from config import FYERS_REDIRECT_URI

    app_id = os.environ.get("FYERS_APP_ID", "").strip()
    secret = os.environ.get("FYERS_SECRET_KEY", "").strip()
    redirect = FYERS_REDIRECT_URI.strip()

    if not app_id or not secret:
        print("Set FYERS_APP_ID and FYERS_SECRET_KEY in .env first (from My API app page).")
        return 1

    if "your_secret" in secret.lower() or len(secret) < 8:
        print("FYERS_SECRET_KEY still looks like a placeholder — use the real Secret from Fyers dashboard.")
        return 1

    print()
    print("=" * 72)
    print("FYERS REDIRECT — fix \"redirectUrl mismatch\"")
    print("=" * 72)
    print("This string is sent to Fyers as redirect_uri (must match app settings EXACTLY):")
    print(f"  {redirect!r}")
    print()
    print("Do ONE of these (same text in both places):")
    print("  A) In browser: https://myapi.fyers.in/dashboard → your app → Edit")
    print("     Paste the line above into \"Redirect URL\" → Save.")
    print("  B) Or copy Redirect URL FROM that page INTO .env as FYERS_REDIRECT_URI=...")
    print()
    print("Typical values (pick the one that matches your app, or align app to this):")
    print("  https://trade.fyers.in/api-login/redirect-uri/index.html")
    print("  https://127.0.0.1")
    print("Check: https not http, hyphens api-login not spaces, no extra trailing / unless app has it.")
    print("=" * 72)
    print()

    q = urllib.parse.urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "state": "state_optional",
        }
    )
    auth_url = f"{FYERS_API}/generate-authcode?{q}"

    print("--- Step 1 ---")
    print("Open ONLY this link (do not open the redirect page by itself first):")
    print(auth_url)
    print()
    print("--- Step 2 ---")
    print("After login, the browser goes to your redirect URL with ?auth_code=... or ?code=...")
    raw = input("Paste the FULL redirect URL (or paste only the auth_code / code value): ").strip()
    if not raw:
        print("No input.")
        return 1

    code = raw
    if "http" in raw:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        for key in ("auth_code", "code"):
            if key in qs and qs[key]:
                code = qs[key][0]
                break
        else:
            print("Could not find code or auth_code in URL query string.")
            return 1

    import requests

    body = {
        "grant_type": "authorization_code",
        "appIdHash": _app_id_hash(app_id, secret),
        "code": code.strip(),
    }
    r = requests.post(
        f"{FYERS_API}/validate-authcode",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=45,
    )
    try:
        data = r.json()
    except Exception:
        print("Non-JSON response:", r.text[:500])
        return 1

    token = data.get("access_token")
    if not token and isinstance(data.get("data"), dict):
        token = data["data"].get("access_token")

    if data.get("s") == "error" or not token:
        print("Fyers response:", json.dumps(data, indent=2)[:800])
        print()
        print("Common fixes: wrong secret, code already used (generate a new login), redirect_uri mismatch.")
        return 1
    print()
    print("--- Step 3 ---")
    print("Add this line to your .env (single line, no spaces around =):")
    print()
    print(f"FYERS_ACCESS_TOKEN={token}")
    print()
    print("Then run:  python scripts/test_fyers.py")
    print("Then run:  python scripts/setup_github_secrets.py   # for GitHub Actions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
