#!/usr/bin/env python3
"""
Discover Telegram chat ID and verify the bot can send messages.

Run after adding the bot to your group:
  https://t.me/Stock_IntradaySuvobot?startgroup

Then send any message in the group and run:
  python telegram_setup.py
  python telegram_setup.py --wait   # polls up to 2 minutes
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

from config import TELEGRAM_CHAT_ID, TELEGRAM_GROUP_CHAT_ID, TELEGRAM_TOKEN, telegram_chat_ids

ENV_PATH = __import__("pathlib").Path(__file__).resolve().parent / ".env"
BOT_USERNAME = "Stock_IntradaySuvobot"
ADD_TO_GROUP_URL = f"https://t.me/{BOT_USERNAME}?startgroup"


def api(method: str, **params):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    if method in ("getUpdates", "getMe", "getWebhookInfo", "getChat"):
        return requests.get(url, params=params, timeout=30).json()
    return requests.post(url, json=params, timeout=30).json()


def fetch_updates() -> list[dict]:
    data = api(
        "getUpdates",
        timeout=0,
        allowed_updates=json.dumps(
            ["message", "edited_message", "my_chat_member", "chat_member"]
        ),
    )
    if not data.get("ok"):
        return []
    return data.get("result", [])


def discover_chat_ids(updates: list[dict]) -> tuple[list[str], list[str]]:
    """Returns (group_ids, private_ids)."""
    groups: list[str] = []
    private: list[str] = []
    for update in updates:
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
            block = update.get(key)
            if not block:
                continue
            chat = block.get("chat")
            if not chat or "id" not in chat:
                continue
            cid = str(chat["id"])
            if chat.get("type") in ("group", "supergroup"):
                if cid not in groups:
                    groups.append(cid)
            elif chat.get("type") == "private" and cid not in private:
                private.append(cid)
    return groups, private


def chat_id_candidates(manual: str | None, discovered: list[str]) -> list[str]:
    out: list[str] = []
    for cid in discovered + ([manual] if manual else []):
        if not cid or cid in out:
            continue
        out.append(cid)
        if cid.lstrip("-").isdigit() and not cid.startswith("-100"):
            alt = f"-100{cid.lstrip('-')}"
            if alt not in out:
                out.append(alt)
    return out


def try_send(chat_id: str, text: str) -> dict:
    return api("sendMessage", chat_id=chat_id, text=text)


def update_env(private_id: str | None = None, group_id: str | None = None) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out: list[str] = []
    seen_private = seen_group = False
    for line in lines:
        if line.startswith("TELEGRAM_CHAT_ID=") and private_id:
            out.append(f"TELEGRAM_CHAT_ID={private_id}")
            seen_private = True
        elif line.startswith("TELEGRAM_GROUP_CHAT_ID=") and group_id:
            out.append(f"TELEGRAM_GROUP_CHAT_ID={group_id}")
            seen_group = True
        else:
            out.append(line)
    if private_id and not seen_private:
        out.append(f"TELEGRAM_CHAT_ID={private_id}")
    if group_id and not seen_group:
        out.append(f"TELEGRAM_GROUP_CHAT_ID={group_id}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def print_help() -> None:
    print(
        f"""
Telegram bot is running, but it is NOT in your group yet.

Do these steps on your phone or Telegram Web:

  1) Open this link and choose your group "Stock Intraday Bot":
     {ADD_TO_GROUP_URL}

  2) Confirm adding @{BOT_USERNAME} as a member (Admin recommended).

  3) In the group, send:  /start

  4) Run again:
     python telegram_setup.py --wait

Note: The number in web.telegram.org/k/#-5178202553 is NOT enough
until the bot is actually added to that group.
"""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup Telegram for trading scanner")
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll Telegram up to 2 minutes for bot join / messages",
    )
    args = parser.parse_args()

    if not TELEGRAM_TOKEN:
        print("Set TELEGRAM_TOKEN in .env first.")
        return 1

    me = api("getMe")
    if not me.get("ok"):
        print("Invalid token:", me)
        return 1
    print("Bot:", me["result"].get("username"))

    updates: list[dict] = []
    if args.wait:
        print("Waiting up to 120s — add the bot to the group and send /start ...")
        for i in range(40):
            updates = fetch_updates()
            if updates:
                print(f"Received {len(updates)} update(s).")
                break
            time.sleep(3)
            if i % 5 == 4:
                print("  still waiting...")
    else:
        updates = fetch_updates()

    groups, private = discover_chat_ids(updates)
    if groups:
        print("Discovered GROUP id(s):", ", ".join(groups))
    if private:
        print("Discovered private id(s):", ", ".join(private))

    if groups:
        update_env(group_id=groups[0])
    if private:
        update_env(private_id=private[0])

    if not updates:
        print_help()
        if not args.wait:
            print("Tip: use  python telegram_setup.py --wait  while you add the bot.\n")
        return 1

    ok = 0
    for cid in chat_id_candidates(None, groups + private) + chat_id_candidates(
        TELEGRAM_GROUP_CHAT_ID, []
    ) + chat_id_candidates(TELEGRAM_CHAT_ID, []):
        if cid in [x for x in telegram_chat_ids()]:
            pass
        print(f"Trying chat_id={cid} ...")
        result = try_send(cid, "Scanner connected to Stock Intraday Bot.")
        if result.get("ok"):
            print("  Message sent.")
            ok += 1
            chat = result.get("result", {}).get("chat", {})
            if chat.get("type") in ("group", "supergroup"):
                update_env(group_id=str(chat["id"]))
            elif chat.get("type") == "private":
                update_env(private_id=str(chat["id"]))
        else:
            print("  Failed:", result.get("description", result))

    if ok:
        print(f"\nSuccess: {ok} destination(s). Alerts will go to all working chats in .env")
        return 0

    print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
