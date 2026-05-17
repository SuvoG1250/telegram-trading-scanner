"""Telegram alert delivery."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from config import TELEGRAM_TOKEN, telegram_chat_ids
from risk import TradeLevels

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    strategy: str
    side: str  # BUY or SELL
    levels: TradeLevels
    note: str = ""


def _escape_md(text: str) -> str:
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_signal_message(signal: Signal) -> str:
    lv = signal.levels
    side_emoji = "🟢" if signal.side == "BUY" else "🔴"
    lines = [
        f"{side_emoji} *{_escape_md(signal.side)} Signal*",
        "",
        f"*Stock:* `{_escape_md(signal.symbol)}`",
        f"*Strategy:* {_escape_md(signal.strategy)}",
        f"*Entry:* `{lv.entry}`",
        f"*Stop Loss:* `{lv.stop_loss}`",
        "",
        "*Targets*",
        f"• T1 \\(1:1\\.5 RR\\): `{lv.target_1}`",
        f"• T2 \\(1:2 RR\\): `{lv.target_2}`",
        f"• T3: {_escape_md(lv.trailing_note)}",
        "",
        f"_Risk:_ `{lv.risk}` | _R1:_ `{lv.reward_1}` | _R2:_ `{lv.reward_2}`",
    ]
    if signal.note:
        lines.extend(["", f"_{_escape_md(signal.note)}_"])
    return "\n".join(lines)


def _api_error(resp: requests.Response) -> str:
    try:
        body = resp.json()
        return body.get("description", resp.text)
    except Exception:
        return resp.text


def _post_message(chat_id: str, payload: dict) -> requests.Response:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {**payload, "chat_id": chat_id}
    return requests.post(url, json=payload, timeout=30)


def _send_to_all(payload: dict) -> bool:
    chat_ids = telegram_chat_ids()
    if not TELEGRAM_TOKEN or not chat_ids:
        logger.warning("Telegram credentials missing; message not sent.")
        return False

    ok_any = False
    for chat_id in chat_ids:
        try:
            resp = _post_message(chat_id, payload)
            if resp.ok:
                ok_any = True
            else:
                err = _api_error(resp)
                logger.error("Telegram send to %s failed: %s", chat_id, err)
        except requests.RequestException:
            logger.exception("Telegram send to %s failed", chat_id)
    return ok_any


def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not telegram_chat_ids():
        logger.warning("Telegram credentials missing; message not sent.")
        logger.info("Message preview:\n%s", message)
        return False

    return _send_to_all(
        {
            "text": message,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
    )


def send_signal(signal: Signal) -> bool:
    return send_telegram(format_signal_message(signal))


def send_plain(text: str) -> bool:
    if not TELEGRAM_TOKEN or not telegram_chat_ids():
        logger.warning("Telegram credentials missing. Run: python telegram_setup.py")
        return False
    return _send_to_all({"text": text})
