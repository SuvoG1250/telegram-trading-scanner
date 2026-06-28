"""Pre-market market summary — full analysis or compact news + sentiment (Bengali)."""

from __future__ import annotations

import logging

from config import SEND_PREMARKET_FULL_ANALYSIS, SEND_PREMARKET_MARKET_SUMMARY
from market_news import build_market_news_digest, classify_headlines
from market_sentiment import assess_market_sentiment
from market_time import is_premarket_summary_window, now_ist
from state import mark_premarket_summary_sent, premarket_summary_sent
from telegram_client import send_plain

logger = logging.getLogger(__name__)

_BIAS_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "positive": "🟢", "negative": "🔴", "mixed": "🟡"}

_BIAS_BN = {
    "bullish": "বুলিশ (ইতিবাচক)",
    "bearish": "বিয়ারিশ (নেতিবাচক)",
    "neutral": "নিরপেক্ষ",
    "positive": "ইতিবাচক",
    "negative": "নেতিবাচক",
    "mixed": "মিশ্র",
    "gap_up": "gap-up",
    "gap_down": "gap-down",
    "flat": "স্থির / flat",
}


def _overall_bias(sentiment: dict, news_bias: str) -> str:
    trade = sentiment.get("trade_bias", "neutral")
    if trade == news_bias and trade in ("bullish", "bearish"):
        return trade
    if trade == "bullish" or news_bias == "bullish":
        if trade == "bearish" or news_bias == "bearish":
            return "neutral"
        return "bullish"
    if trade == "bearish" or news_bias == "bearish":
        return "bearish"
    return "neutral"


def format_premarket_market_summary() -> str:
    sentiment = assess_market_sentiment()
    news = build_market_news_digest()
    positive, negative, neutral = classify_headlines(news.headlines)
    overall = _overall_bias(sentiment, news.news_bias)
    bias_emoji = _BIAS_EMOJI.get(overall, "⚪")
    ts = now_ist().strftime("%d %b %Y, %H:%M IST")

    lines = [
        f"📰 <b>প্রি-মার্কেট সারাংশ</b> — {ts}",
        "",
        (
            f"{bias_emoji} <b>আজকের bias:</b> {_BIAS_BN.get(overall, overall)} "
            f"| খবর: {_BIAS_BN.get(news.news_bias, news.news_bias)} "
            f"| Nifty {sentiment.get('nifty_gap_pct', 0):+.2f}% "
            f"| Global: {_BIAS_BN.get(sentiment.get('global', 'mixed'), sentiment.get('global', 'mixed'))}"
        ),
        "",
        "<b>✅ ইতিবাচক খবর</b>",
    ]
    if positive:
        lines.extend(f"• {h}" for h in positive[:6])
    else:
        lines.append("• feed-এ স্পষ্ট ইতিবাচক headline নেই")

    lines.extend(["", "<b>❌ নেতিবাচক / ঝুঁকি</b>"])
    if negative:
        lines.extend(f"• {h}" for h in negative[:6])
    else:
        lines.append("• feed-এ স্পষ্ট নেতিবাচক headline নেই")

    if neutral:
        lines.extend(["", "<b>➖ মিশ্র / নিরপেক্ষ</b>"])
        lines.extend(f"• {h}" for h in neutral[:4])

    nifty_gap = sentiment.get("nifty_gap", "flat")
    global_m = sentiment.get("global", "mixed")
    context = (
        f"Nifty: {_BIAS_BN.get(nifty_gap, nifty_gap)} ({sentiment.get('nifty_gap_pct', 0):+.2f}%) | "
        f"Global: {_BIAS_BN.get(global_m, global_m)} | Bias: {_BIAS_BN.get(overall, overall)}"
    )
    lines.extend(
        [
            "",
            "<b>📊 বাজার context</b>",
            context,
            "<i>(FII/DII: NSE-তে manually verify করুন — free feed-এ নেই)</i>",
            "",
            f"<i>{len(news.headlines)} headline scan · yfinance + Google News</i>",
        ]
    )
    return "\n".join(lines)


def send_premarket_market_summary(*, force_window: bool = True) -> bool:
    if not SEND_PREMARKET_MARKET_SUMMARY or premarket_summary_sent():
        return False
    if force_window and not is_premarket_summary_window():
        return False
    try:
        if SEND_PREMARKET_FULL_ANALYSIS:
            from premarket_analysis import format_full_premarket_analysis

            text = format_full_premarket_analysis(use_llm=True)
        else:
            text = format_premarket_market_summary()
    except Exception:
        logger.exception("Pre-market summary build failed — sending compact fallback.")
        text = format_premarket_market_summary()
    if send_plain(text):
        mark_premarket_summary_sent()
        logger.info("Pre-market market summary sent.")
        return True
    logger.error("Failed to send pre-market market summary.")
    return False
