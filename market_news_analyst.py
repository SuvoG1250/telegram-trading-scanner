"""Bengali 24-hour global + NSE market news analyst for Telegram."""

from __future__ import annotations

import logging
import re
from typing import Any

from config import SEND_BENGALI_NEWS_ANALYSIS
from gemini_client import gemini_generate, llm_available
from market_news import (
    build_24h_market_news_digest,
    classify_headlines,
    rank_headlines_by_impact,
)
from market_time import now_ist
from state import bengali_news_sent, mark_bengali_news_sent
from telegram_client import send_plain

logger = logging.getLogger(__name__)

_BULLISH = re.compile(
    r"\b(rally|surge|gain|gains|positive|beats?|strong|growth|record|"
    r"rebound|bullish|inflow|upgrade|cut|eases?|recovery)\b",
    re.I,
)
_BEARISH = re.compile(
    r"\b(fall|falls|crash|drop|drops|negative|miss|weak|selloff|concern|"
    r"bearish|outflow|downgrade|war|inflation|hike|tariff|sanction|slump)\b",
    re.I,
)

_BIAS_BN = {
    "bullish": "ইতিবাচক (বুলিশ)",
    "bearish": "নেতিবাচক (বিয়ারিশ)",
    "neutral": "মিশ্র / নিরপেক্ষ",
    "positive": "ইতিবাচক",
    "negative": "নেতিবাচক",
    "mixed": "মিশ্র",
}


def _headline_sentiment(title: str) -> str:
    bull = len(_BULLISH.findall(title))
    bear = len(_BEARISH.findall(title))
    if bull > bear:
        return "positive"
    if bear > bull:
        return "negative"
    return "neutral"


def _fallback_impact_bengali(title: str) -> str:
    """Rule-based Bengali impact line when LLM is unavailable."""
    t = title.lower()
    sentiment = _headline_sentiment(title)

    if any(k in t for k in ("rbi", "repo rate", "fed", "fomc", "rate hike", "rate cut")):
        if sentiment == "positive" or "cut" in t or "ease" in t:
            return (
                "সুদের হার কমার খবর ব্যাংক ও রিয়েলটি সেক্টরে ইতিবাচক; "
                "Nifty-তে gap-up বা intraday rally-র সম্ভাবনা।"
            )
        if sentiment == "negative" or "hike" in t:
            return (
                "সুদ বাড়ার আশঙ্কা FII outflow ও valuation compression-এর ঝুঁকি; "
                "IT ও midcap-এ চাপ পড়তে পারে।"
            )
        return "নীতিনির্ধারক খবর — আজ/আগামী সেশনে volatility বাড়তে পারে; position size ছোট রাখুন।"

    if any(k in t for k in ("fii", "dii", "foreign institutional")):
        if sentiment == "positive" or "buy" in t or "inflow" in t:
            return "FII কেনার প্রবণতা Nifty/Sensex-এ support দিতে পারে; dip-এ buying interest দেখা যেতে পারে।"
        if sentiment == "negative" or "sell" in t or "outflow" in t:
            return "FII বিক্রির খবর index-এ weakness ও sector rotation-এর ঝুঁকি বাড়ায়।"
        return "FII/DII flow আজকের direction নির্ধারণে গুরুত্বপূর্ণ; opening gap-এর সাথে মিলিয়ে দেখুন।"

    if any(k in t for k in ("crude", "oil", "brent", "wti")):
        if sentiment == "negative" or "rise" in t or "surge" in t:
            return "তেল দাম বাড়লে INR দুর্বল ও OMC/Aviation-এ চাপ; inflation concern বাড়ে।"
        return "তেল দাম কমলে INR ও inflation outlook উন্নত; consumer ও OMC সেক্টরে relief।"

    if any(k in t for k in ("rupee", "dollar", "forex", "usd/inr")):
        if "weak" in t or "fall" in t or "depreciat" in t:
            return "টাকা দুর্বল হলে FII outflow ও import-heavy স্টকে চাপ; IT exporter-দের কিছুটা সুবিধা।"
        return "টাকা শক্তিশালী হলে FII confidence বাড়তে পারে; broader market-এ সহায়ক।"

    if any(k in t for k in ("earnings", "results", "quarterly", "profit", "revenue")):
        if sentiment == "positive" or "beat" in t:
            return "ফলাফল expectations-এর চেয়ে ভালো — সংশ্লিষ্ট স্টক ও sector-এ short-term rally সম্ভব।"
        if sentiment == "negative" or "miss" in t:
            return "ফলাফল নিরাশাজনক — স্টক-specific correction ও sector drag দেখা যেতে পারে।"
        return "ফলাফল season — stock-specific movement বেশি; index-wide impact সীমিত হতে পারে।"

    if any(k in t for k in ("nifty", "sensex", "nse", "bse", "india market")):
        if sentiment == "positive":
            return "দেশীয় সূচক-সংক্রান্ত ইতিবাচক খবর — আজ opening-এ bullish bias সম্ভব।"
        if sentiment == "negative":
            return "দেশীয় বাজার-সংক্রান্ত নেতিবাচক খবর — gap-down বা intraday weakness-এর ঝুঁকি।"
        return "সূচক-সংক্রান্ত খবর — trend confirm করার জন্য global cue ও FII flow দেখুন।"

    if any(k in t for k in ("nasdaq", "s&p", "dow", "wall street", "us market", "global")):
        if sentiment == "positive":
            return "গ্লোবাল rally Gift Nifty/Nifty-তে gap-up cue দিতে পারে; IT ও largecap-এ সুবিধা।"
        if sentiment == "negative":
            return "বিদেশি বাজার দুর্বল — Asian session ও Nifty opening-এ negative bias সম্ভব।"
        return "গ্লোবাল cue mixed — opening volatility বেশি; first 30 min trend follow করুন।"

    if any(k in t for k in ("war", "geopolit", "tariff", "sanction", "conflict")):
        return "ভূ-রাজনৈতিক/ট্রেড ঝুঁকি — risk-off mood, defence/gold শক্ত; broader market-এ uncertainty।"

    if sentiment == "positive":
        return "সাধারণত ইতিবাচক headline — সংশ্লিষ্ট সেক্টর/স্টকে short-term upside bias।"
    if sentiment == "negative":
        return "সাধারণত নেতিবাচক headline — profit booking ও defensive positioning বাড়তে পারে।"
    return "স্পষ্ট direction নেই — headline monitor করুন; major support/resistance-এ reaction দেখুন।"


def gather_analyst_context() -> dict[str, Any]:
    """Collect 24h headlines, FII/DII, globals for Bengali briefing."""
    from premarket_analysis import fetch_fii_dii, gather_premarket_data

    digest = build_24h_market_news_digest()
    positive, negative, neutral = classify_headlines(digest.headlines)
    ranked = rank_headlines_by_impact(digest.headlines, top_n=10)

    pre = gather_premarket_data()
    fii = fetch_fii_dii()

    return {
        "timestamp": now_ist().strftime("%d %b %Y, %H:%M IST"),
        "news_bias": digest.news_bias,
        "headline_count": len(digest.headlines),
        "top_headlines": [{"title": t, "score": s} for t, s in ranked],
        "positive": positive[:6],
        "negative": negative[:6],
        "neutral": neutral[:4],
        "fii": fii,
        "globals": pre.get("globals") or {},
        "gift_pts": pre.get("gift_pts"),
        "overview": pre.get("overview") or [],
        "economic": pre.get("economic") or [],
        "stocks_focus": pre.get("stocks_focus") or [],
        "sentiment": pre.get("sentiment") or {},
    }


def _build_llm_prompt(ctx: dict[str, Any]) -> str:
    headlines = "\n".join(
        f"- [{h['score']}] {h['title']}" for h in ctx.get("top_headlines") or []
    )
    globals_lines = "\n".join(
        f"- {k}: {v}" for k, v in (ctx.get("globals") or {}).items()
    )
    fii = ctx.get("fii") or {}
    fii_line = f"FII: {fii.get('fii_cash', 'n/a')} | DII: {fii.get('dii_cash', 'n/a')}"

    return f"""Act as an expert financial analyst covering global and Indian (NSE/BSE) markets.

Write in SIMPLE, CLEAR BENGALI (Bangla Unicode script — NOT romanized Bengali).

Task: Summarize the most crucial stock-specific and macroeconomic news from the past 24 hours and explain how each item may impact TODAY or TOMORROW's Indian market session.

Use Telegram HTML sparingly: only <b> for section headers. No markdown.

Structure:
<b>📰 ২৪ ঘণ্টার বাজার সারাংশ</b>
(2-3 sentence overall summary in Bengali)

<b>🌍 গুরুত্বপূর্ণ খবর ও প্রভাব বিশ্লেষণ</b>
For each major news item (5-8 items max), write:
• Headline (short Bengali translation or keep English name if stock-specific)
• প্রভাব: ইতিবাচক / নেতিবাচক / মিশ্র
• বিশ্লেষণ: 2-3 simple Bengali sentences on Nifty/Sensex/sectors/stocks

<b>📊 ম্যাক্রো ও ফ্লো</b>
(FII/DII, global cues, crude/rupee if relevant)

<b>🎯 আজ/আগামীকালের দৃষ্টিভঙ্গি</b>
(Overall bias: bullish/bearish/sideways + key levels or sectors to watch)

Keep total under 3500 characters. Be specific about impact direction.

DATA ({ctx.get('timestamp')}):
News bias: {ctx.get('news_bias')}
{fii_line}
Gift Nifty pts: {ctx.get('gift_pts')}
Global cues:
{globals_lines or 'n/a'}

Top headlines (impact score):
{headlines or 'none'}

Positive headlines:
{chr(10).join('- ' + h for h in ctx.get('positive') or []) or 'none'}

Negative headlines:
{chr(10).join('- ' + h for h in ctx.get('negative') or []) or 'none'}

Stocks in focus:
{chr(10).join('- ' + h for h in ctx.get('stocks_focus') or []) or 'none'}

Economic events:
{chr(10).join('- ' + h for h in ctx.get('economic') or []) or 'none'}
"""


def format_fallback_bengali_analysis(ctx: dict[str, Any] | None = None) -> str:
    """Rule-based Bengali briefing when LLM is unavailable."""
    ctx = ctx or gather_analyst_context()
    ts = ctx.get("timestamp", "")
    bias = ctx.get("news_bias", "neutral")
    bias_bn = _BIAS_BN.get(bias, bias)

    lines = [
        f"<b>📰 ২৪ ঘণ্টার বাজার খবর বিশ্লেষণ</b>",
        f"<i>{ts}</i>",
        "",
        f"<b>সারাংশ:</b> গত ২৪ ঘণ্টায় {ctx.get('headline_count', 0)}টি headline স্ক্যান করা হয়েছে। "
        f"সামগ্রিক headline bias: <b>{bias_bn}</b>।",
    ]

    gift = ctx.get("gift_pts")
    if gift is not None:
        direction = "ইতিবাচক cue" if gift >= 0 else "নেতিবাচক cue"
        lines.append(f"Gift Nifty: {gift:+.0f} pts — {direction}।")

    fii = ctx.get("fii") or {}
    if fii.get("fii_cash") or fii.get("dii_cash"):
        lines.extend(
            [
                "",
                "<b>📊 FII / DII (cash):</b>",
                f"FII: {fii.get('fii_cash', 'n/a')} | DII: {fii.get('dii_cash', 'n/a')}",
            ]
        )

    globals_map = ctx.get("globals") or {}
    if globals_map:
        lines.extend(["", "<b>🌍 গ্লোবাল cue:</b>"])
        for label, pts in list(globals_map.items())[:5]:
            if pts is None:
                continue
            sign = "↑" if pts >= 0 else "↓"
            lines.append(f"• {label}: {pts:+.0f} pts {sign}")

    lines.extend(["", "<b>🔍 গুরুত্বপূর্ণ খবর ও সম্ভাব্য প্রভাব:</b>"])
    ranked = ctx.get("top_headlines") or []
    if not ranked:
        lines.append("• কোনো headline fetch হয়নি — নেটওয়ার্ক চেক করুন।")
    else:
        for i, item in enumerate(ranked[:8], 1):
            title = item.get("title", "")
            sentiment = _headline_sentiment(title)
            sent_bn = _BIAS_BN.get(sentiment, sentiment)
            impact = _fallback_impact_bengali(title)
            lines.extend(
                [
                    "",
                    f"<b>{i}. {title[:120]}</b>",
                    f"প্রভাব: {sent_bn}",
                    impact,
                ]
            )

    lines.extend(
        [
            "",
            "<b>🎯 আজ/আগামীকালের দৃষ্টিভঙ্গি:</b>",
        ]
    )
    if bias == "bullish":
        lines.append(
            "Headline ও global cue মিল রেখে short-term bullish bias। "
            "Gap-up হলে opening fade না হওয়া পর্যন্ত trend follow; Bank Nifty / Nifty leader দেখুন।"
        )
    elif bias == "bearish":
        lines.append(
            "Risk-off headline dominance — gap-down বা weak opening-এ defensive play। "
            "Support break হলে further fall; FII flow confirm করুন।"
        )
    else:
        lines.append(
            "Mixed cues — range-bound session সম্ভব। "
            "Opening 30 min-এ direction clear হওয়ার পর trade নিন; news-driven volatility সতর্ক থাকুন।"
        )

    lines.append("")
    lines.append("<i>Rule-based Bengali summary · LLM unavailable · yfinance + Google News</i>")
    return "\n".join(lines)


def generate_bengali_market_analysis(*, use_llm: bool = True) -> str:
    ctx = gather_analyst_context()
    if use_llm and llm_available():
        prompt = _build_llm_prompt(ctx)
        text = gemini_generate(prompt, max_tokens=1800, temperature=0.35)
        if text and len(text.strip()) > 200:
            footer = f"\n\n<i>{ctx['timestamp']} · AI analyst · {ctx.get('headline_count', 0)} headlines</i>"
            return text.strip() + footer
        logger.warning("LLM Bengali analysis too short; using fallback.")
    return format_fallback_bengali_analysis(ctx)


def format_bengali_market_news_analysis() -> str:
    return generate_bengali_market_analysis(use_llm=True)


def send_bengali_market_news_analysis(*, force: bool = False) -> bool:
    if not SEND_BENGALI_NEWS_ANALYSIS:
        logger.info("Bengali news analysis disabled (SEND_BENGALI_NEWS_ANALYSIS=false).")
        return False
    if not force and bengali_news_sent():
        logger.debug("Bengali news already sent today.")
        return False
    try:
        body = format_bengali_market_news_analysis()
    except Exception:
        logger.exception("Bengali market news analysis failed")
        return False
    if not body:
        return False
    ok = send_plain(body, html_mode=True)
    if ok:
        mark_bengali_news_sent()
    return ok
