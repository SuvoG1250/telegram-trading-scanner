"""Daily Telegram strategy authorization — alerts on, execution paused until button tap."""

from __future__ import annotations

import logging

import requests

from config import (
    DAILY_STRATEGY_PROMPT_HOUR,
    DAILY_STRATEGY_PROMPT_MINUTE,
    SEND_DAILY_STRATEGY_PROMPT,
    TELEGRAM_TOKEN,
    telegram_chat_ids,
)
from market_time import is_weekday, now_ist
from state import daily_strategy_prompt_sent, mark_daily_strategy_prompt_sent

logger = logging.getLogger(__name__)

DAILY_SETUP_MESSAGE = (
    "📊 <b>Algo Trading Daily Setup</b> 📊\n\n"
    "Good Morning! The market is getting ready.\n\n"
    "Both strategies are active in the background. "
    "Please select which strategy you want to authorize for "
    "<b>LIVE Upstox execution</b> today:\n\n"
    "📉 <b>Option 1:</b> Original Strategy (ST+TSL · 5m)\n"
    "📈 <b>Option 2:</b> 9/21 EMA + MACD Strategy (3m HA)\n\n"
    "After strategy, you'll pick <b>Nifty</b> or <b>Sensex</b> "
    "(GTT SL/Target points apply per index).\n\n"
    "Tap a button below to confirm your choice:"
)


def daily_strategy_markup() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🟢 Run Original Strategy", "callback_data": "exec:st_tsl"}],
            [{"text": "🔵 Run EMA+MACD Strategy", "callback_data": "exec:ema_macd_sync"}],
            [{"text": "🔴 Pause Trading Today", "callback_data": "exec:pause"}],
        ]
    }


def is_daily_strategy_prompt_window() -> bool:
    if not is_weekday():
        return False
    t = now_ist()
    start = (DAILY_STRATEGY_PROMPT_HOUR, DAILY_STRATEGY_PROMPT_MINUTE)
    end = (9, 35)
    cur = (t.hour, t.minute)
    return start <= cur < end


def reset_daily_execution_authorization() -> None:
    """Pause Upstox auto-orders until user taps a strategy button."""
    from upstox_trade_state import pause_live_execution

    pause_live_execution(clear_strategy=True)


def _send_to_chat(chat_id: str, text: str, markup: dict) -> bool:
    if not TELEGRAM_TOKEN:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": markup,
            },
            timeout=20,
        )
        body = resp.json() if resp.content else {}
        return bool(resp.ok and body.get("ok"))
    except requests.RequestException:
        logger.exception("Daily strategy prompt send failed for chat %s", chat_id)
        return False


def send_daily_strategy_setup(*, force: bool = False) -> bool:
    """
    Morning prompt with inline buttons. Resets execution to PAUSED until user selects.
    """
    if not SEND_DAILY_STRATEGY_PROMPT:
        return False
    if not force and not is_daily_strategy_prompt_window():
        return False
    if daily_strategy_prompt_sent():
        return False

    from upstox_execution_strategy import get_execution_strategy
    from upstox_trade_state import get_mode

    if get_execution_strategy() and get_mode() == "live":
        mark_daily_strategy_prompt_sent()
        logger.info("Strategy already authorized today — skipping daily prompt.")
        return False

    reset_daily_execution_authorization()
    markup = daily_strategy_markup()
    sent_any = False
    for chat_id in telegram_chat_ids():
        if _send_to_chat(chat_id, DAILY_SETUP_MESSAGE, markup):
            sent_any = True

    if sent_any:
        mark_daily_strategy_prompt_sent()
        logger.info("Daily strategy setup prompt sent (execution paused).")
    return sent_any


def confirmation_message(strategy_key: str, *, index_key: str | None = None) -> str:
    from upstox_execution_index import INDEX_LABELS
    from upstox_execution_strategy import STRATEGY_LABELS

    if strategy_key == "pause":
        return (
            "🔴 <b>Confirmed!</b> Live execution is <b>PAUSED</b> for today.\n"
            "Background alerts will continue — no Upstox orders will be placed."
        )
    label = STRATEGY_LABELS.get(strategy_key, strategy_key)
    idx_line = ""
    if index_key and index_key in INDEX_LABELS:
        sl_tgt = "SL 15 / Target 30" if index_key == "nifty" else "SL 20 / Target 50"
        idx_line = f"\n<b>Index:</b> {INDEX_LABELS[index_key]} · GTT {sl_tgt}"
    return (
        f"✅ <b>Confirmed!</b> Live execution started for <b>{label}</b>{idx_line}\n"
        f"GTT entry = exact alert premium (zero buffer)."
    )
