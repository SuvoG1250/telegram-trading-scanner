"""Professional Telegram trade alerts (HTML format)."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Literal

import requests

from config import TELEGRAM_TOKEN, telegram_chat_ids
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
    """Professional trader-style alert: Stock, Entry, SL, Best Target."""
    sym = html.escape(signal.symbol)
    strat = html.escape(signal.strategy)
    ts = html.escape(signal.timestamp or "")
    lv = signal.levels

    if signal.kind == "EXIT":
        side_label = "EXIT / BOOK PROFIT" if signal.side == "SELL" else "EXIT POSITION"
        emoji = "🏁"
        return (
            f"{emoji} <b>{side_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 <b>Stock:</b> {sym} <i>(NSE)</i>\n"
            f"📊 <b>Strategy:</b> {strat}\n"
            f"🕐 <b>Time:</b> {ts}\n\n"
            f"💵 <b>Exit near:</b> ₹{lv.entry:,.2f}\n\n"
            f"📋 <b>Action:</b> {html.escape(signal.note or 'Close the open position.')}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    is_buy = signal.side == "BUY"
    emoji = "🟢" if is_buy else "🔴"
    action = "BUY (LONG)" if is_buy else "SELL (SHORT)"
    best = lv.primary_target

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
        f"🎯 <b>BEST TARGET:</b> ₹{best:,.2f} <i>(1:{lv.rr_best} R:R)</i>",
        f"📈 <b>T1:</b> ₹{lv.target_1:,.2f}",
        f"📈 <b>T2:</b> ₹{lv.target_2:,.2f}",
        "",
        f"⚖️ <b>Risk:</b> ₹{lv.risk:,.2f} ({lv.risk_pct}%)",
        f"✅ <b>Reward (best):</b> ₹{abs(best - lv.entry):,.2f}",
        f"📐 <b>R:R</b> = 1 : {lv.risk_reward_best}",
        "",
        "<b>📋 Trade plan</b>",
        "• Honor stop loss — no averaging down",
        "• Book 40–50% at T1, rest toward best target",
        f"• {html.escape(lv.trailing_note)}",
        "• <b>Square off ALL intraday positions by 3:30 PM IST</b>",
    ]
    if signal.note:
        lines.extend(["", f"<b>Setup:</b> {html.escape(signal.note)}"])
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
        "Signal | %s | %s | %s | Entry=%s SL=%s Target=%s",
        signal.strategy,
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
