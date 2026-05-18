"""Telegram trade alerts — compact signals-only format by default."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Literal

import requests

from config import SIGNALS_ONLY_TELEGRAM, TELEGRAM_TOKEN, telegram_chat_ids
from risk import TradeLevels

logger = logging.getLogger(__name__)

SignalKind = Literal["ENTRY", "EXIT"]


@dataclass
class Signal:
    symbol: str
    strategy: str
    side: str  # BUY or SELL
    levels: TradeLevels
    note: str = ""
    kind: SignalKind = "ENTRY"
    timeframe: str = "Intraday"
    timestamp: str = ""


def format_signal_message(signal: Signal) -> str:
    sym = html.escape(signal.symbol)
    ts = html.escape(signal.timestamp or "")
    lv = signal.levels
    target = lv.primary_target

    if signal.kind == "EXIT":
        return (
            f"🏁 <b>EXIT {sym}</b>\n"
            f"₹{lv.entry:,.2f}  ·  {ts}"
        )

    is_buy = signal.side == "BUY"
    emoji = "🟢" if is_buy else "🔴"
    action = "BUY" if is_buy else "SELL"

    if SIGNALS_ONLY_TELEGRAM:
        return (
            f"{emoji} <b>{action} {sym}</b>\n"
            f"Entry <b>₹{lv.entry:,.2f}</b>  ·  SL <b>₹{lv.stop_loss:,.2f}</b>  ·  Target <b>₹{target:,.2f}</b>\n"
            f"<i>1:{lv.risk_reward_best} R:R  ·  {ts}</i>"
        )

    strat = html.escape(signal.strategy)
    lines = [
        f"{emoji} <b>INTRADAY {action}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📌 <b>Stock:</b> {sym} <i>(NSE)</i>",
        f"📊 <b>Setup:</b> {strat}",
        f"⏱ <b>Timeframe:</b> {html.escape(signal.timeframe)}",
        f"🕐 <b>Time:</b> {ts}",
        "",
        f"💰 <b>ENTRY:</b> ₹{lv.entry:,.2f}",
        f"🛑 <b>STOP LOSS:</b> ₹{lv.stop_loss:,.2f}",
        f"🎯 <b>TARGET:</b> ₹{target:,.2f} "
        f"<i>(+{lv.target_profit_pct(signal.side):.2f}% | 1:{lv.risk_reward_best})</i>",
        f"📈 <b>T1:</b> ₹{lv.target_1:,.2f}",
        "",
        "<i>Book 70% at T1 · trail runner · exit by 3:30 PM IST</i>",
    ]
    if signal.note:
        lines.extend(["", html.escape(signal.note)])
    lines.append("━━━━━━━━━━━━━━━━━━━━")
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
                logger.error("Telegram send to %s failed: %s", chat_id, _api_error(resp))
        except requests.RequestException:
            logger.exception("Telegram send to %s failed", chat_id)
    return ok_any


def send_telegram(message: str, *, html_mode: bool = True) -> bool:
    if not TELEGRAM_TOKEN or not telegram_chat_ids():
        logger.warning("Telegram credentials missing; message not sent.")
        return False
    payload: dict = {"text": message, "disable_web_page_preview": True}
    if html_mode:
        payload["parse_mode"] = "HTML"
    return _send_to_all(payload)


def send_signal(signal: Signal) -> bool:
    body = format_signal_message(signal)
    logger.info(
        "Signal | %s %s | Entry=%s SL=%s Target=%s",
        signal.symbol,
        signal.side,
        signal.levels.entry,
        signal.levels.stop_loss,
        signal.levels.primary_target,
    )
    return send_telegram(body, html_mode=True)


def send_plain(text: str, *, html_mode: bool = True) -> bool:
    if not TELEGRAM_TOKEN or not telegram_chat_ids():
        logger.warning("Telegram credentials missing. Run: python telegram_setup.py")
        return False
    payload: dict = {"text": text, "disable_web_page_preview": True}
    if html_mode:
        payload["parse_mode"] = "HTML"
    return _send_to_all(payload)
