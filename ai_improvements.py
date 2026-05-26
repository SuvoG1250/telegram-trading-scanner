"""
Project-wide AI helpers (Gemini primary, Groq fallback).

- Filter weak equity signals before Telegram
- Nifty option alert notes
- End-of-day insight on P/L summary
"""

from __future__ import annotations

import logging

from config import AI_MIN_CONFIDENCE, AI_SIGNAL_VALIDATE
from gemini_client import gemini_generate, gemini_json, llm_available
from market_sentiment import assess_market_sentiment
from sector_map import sector_for

logger = logging.getLogger(__name__)


def ai_features_enabled() -> bool:
    return llm_available()


def should_send_equity_signal(
    *,
    symbol: str,
    side: str,
    strategy: str,
    entry: float,
    stop_loss: float,
    target: float,
    score: float,
    timeframe: str = "",
) -> tuple[bool, str]:
    """
    AI quality gate. Returns (send?, reason).
    Fail-open: if AI unavailable or errors, allow the signal.
    """
    if not AI_SIGNAL_VALIDATE or not ai_features_enabled():
        return True, ""

    prompt = (
        f"Indian NSE intraday trade review. Approve only high-quality setups.\n"
        f"Symbol: {symbol} ({sector_for(symbol)}) | Side: {side} | Strategy: {strategy}\n"
        f"Entry {entry:.2f} SL {stop_loss:.2f} Target {target:.2f} | Tech score {score:.1f}\n"
        f"Market: {assess_market_sentiment().get('summary', '')[:200]}\n"
        'JSON only: {"approve":true|false,"confidence":1-10,"reason":"max 15 words"}'
    )
    data = gemini_json(prompt, max_tokens=120)
    if not isinstance(data, dict):
        return True, ""

    approve = data.get("approve")
    if approve is None:
        approve = str(data.get("decision", "")).lower() in ("yes", "approve", "send", "true")
    else:
        approve = bool(approve)

    try:
        confidence = int(data.get("confidence", 7))
    except (TypeError, ValueError):
        confidence = 7

    reason = str(data.get("reason") or "").strip()[:120]

    if approve or confidence >= AI_MIN_CONFIDENCE:
        return True, reason

    logger.info("AI filter rejected %s %s (confidence=%s): %s", symbol, side, confidence, reason)
    return False, reason or "AI low confidence"


def build_nifty_option_ai_note(
    *,
    side: str,
    strike: float,
    option_type: str,
    entry: float,
    stop_loss: float,
    target: float,
) -> str:
    if not ai_features_enabled():
        return ""
    prompt = (
        f"NSE Nifty intraday {side} {option_type} strike {strike:.0f}. "
        f"Premium entry {entry:.2f} SL {stop_loss:.2f} target {target:.2f}. "
        "Two short bullets: trend context + risk watch. Plain text, max 40 words."
    )
    return gemini_generate(prompt, max_tokens=120, temperature=0.25)[:280]


def build_daily_ai_insight(trade_count: int, net_pnl: float, wins: int, losses: int) -> str:
    if not ai_features_enabled() or trade_count == 0:
        return ""
    prompt = (
        f"NSE intraday day ended. {trade_count} signals, net P/L {net_pnl:+.2f}%, "
        f"wins {wins} losses {losses}. One sentence lesson + one sentence for tomorrow. "
        "Plain text, no HTML."
    )
    return gemini_generate(prompt, max_tokens=100, temperature=0.35)[:300]
