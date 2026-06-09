"""Pre-market market summary — news + sentiment (positive / negative)."""

from __future__ import annotations

import logging

from config import SEND_PREMARKET_MARKET_SUMMARY
from market_news import build_market_news_digest, classify_headlines
from market_sentiment import assess_market_sentiment
from market_time import now_ist
from state import mark_premarket_summary_sent, premarket_summary_sent
from telegram_client import send_plain

logger = logging.getLogger(__name__)

_BIAS_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "positive": "🟢", "negative": "🔴", "mixed": "🟡"}


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
        f"📰 <b>Pre-Market Summary</b> — {ts}",
        "",
        (
            f"{bias_emoji} <b>Today&apos;s bias:</b> {overall} "
            f"| News: {news.news_bias} "
            f"| Nifty {sentiment.get('nifty_gap_pct', 0):+.2f}% "
            f"| Global: {sentiment.get('global', 'mixed')}"
        ),
        "",
        "<b>✅ Positive</b>",
    ]
    if positive:
        lines.extend(f"• {h}" for h in positive[:6])
    else:
        lines.append("• No clearly positive headlines in feed")

    lines.extend(["", "<b>❌ Negative / risks</b>"])
    if negative:
        lines.extend(f"• {h}" for h in negative[:6])
    else:
        lines.append("• No clearly negative headlines in feed")

    if neutral:
        lines.extend(["", "<b>➖ Mixed / neutral</b>"])
        lines.extend(f"• {h}" for h in neutral[:4])

    lines.extend(
        [
            "",
            "<b>📊 Market context</b>",
            sentiment.get("summary", "").replace("\n", " "),
            "",
            f"<i>{len(news.headlines)} headlines scanned · yfinance + Google News</i>",
        ]
    )
    return "\n".join(lines)


def send_premarket_market_summary() -> bool:
    if not SEND_PREMARKET_MARKET_SUMMARY or premarket_summary_sent():
        return False
    text = format_premarket_market_summary()
    if send_plain(text):
        mark_premarket_summary_sent()
        logger.info("Pre-market market summary sent.")
        return True
    logger.error("Failed to send pre-market market summary.")
    return False
