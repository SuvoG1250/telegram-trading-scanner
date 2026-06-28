"""Free market headlines — yfinance + Google News RSS (no paid API)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import yfinance as yf

from market_sentiment import NIFTY_TICKER
from market_time import IST

logger = logging.getLogger(__name__)

_BULLISH = re.compile(
    r"\b(rally|surge|gain|gains|positive|beats?|strong|growth|record high|"
    r"upside|rebound|bullish|inflow|upgrade)\b",
    re.I,
)
_BEARISH = re.compile(
    r"\b(fall|falls|crash|drop|drops|negative|miss|weak|selloff|concern|"
    r"downside|bearish|outflow|downgrade|war|inflation|rate hike)\b",
    re.I,
)
_HIGH_IMPACT = re.compile(
    r"\b(RBI policy|repo rate|Fed|FOMC|US Fed|budget|union budget|"
    r"inflation data|CPI print|GDP data|election result|war|geopolitical|"
    r"trade war|sanctions|rate hike|rate cut decision)\b",
    re.I,
)


def detect_high_impact_events(headlines: list[str]) -> list[str]:
    """Major events → advise NO TRADE for overnight BTST."""
    hits: list[str] = []
    for h in headlines:
        m = _HIGH_IMPACT.search(h)
        if m:
            hits.append(h[:120])
    return hits[:5]

_GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
)


@dataclass
class NewsDigest:
    headlines: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    bullish_hits: int = 0
    bearish_hits: int = 0
    news_bias: str = "neutral"

    def score(self) -> float:
        """Rough -3..+3 from headline keyword counts."""
        if self.bullish_hits > self.bearish_hits + 1:
            return min(3.0, float(self.bullish_hits - self.bearish_hits))
        if self.bearish_hits > self.bullish_hits + 1:
            return max(-3.0, float(self.bearish_hits - self.bullish_hits) * -1)
        return 0.0


def _tag_headline(text: str, digest: NewsDigest) -> None:
    text = (text or "").strip()
    if not text or len(text) < 12:
        return
    digest.headlines.append(text[:200])
    b = len(_BULLISH.findall(text))
    r = len(_BEARISH.findall(text))
    digest.bullish_hits += b
    digest.bearish_hits += r


def _fetch_rss(
    url: str,
    source: str,
    limit: int = 12,
    *,
    max_age_hours: float | None = None,
) -> list[str]:
    titles: list[str] = []
    cutoff = None
    if max_age_hours is not None:
        cutoff = datetime.now(IST) - timedelta(hours=max_age_hours)
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; NSE-Scanner/1.0)"})
        with urlopen(req, timeout=20) as resp:
            root = ET.fromstring(resp.read())
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()
            if cutoff is not None:
                pub_el = item.find("pubDate")
                if pub_el is not None and pub_el.text:
                    try:
                        pub_dt = parsedate_to_datetime(pub_el.text)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=IST)
                        else:
                            pub_dt = pub_dt.astimezone(IST)
                        if pub_dt < cutoff:
                            continue
                    except (TypeError, ValueError, OverflowError):
                        pass
            if title and title not in titles:
                titles.append(title)
            if len(titles) >= limit:
                break
    except Exception:
        logger.warning("RSS fetch failed: %s", source)
    return titles


def fetch_yfinance_nifty_news(limit: int = 8) -> list[str]:
    try:
        items = yf.Ticker(NIFTY_TICKER).news or []
    except Exception:
        logger.warning("yfinance Nifty news unavailable.")
        return []
    out: list[str] = []
    for item in items[:limit]:
        title = (item.get("title") or "").strip()
        if title:
            out.append(title)
    return out


# High-impact keywords for analyst ranking (macro + stock-specific).
_IMPACT_KEYWORDS: tuple[tuple[str, int], ...] = (
    ("rbi", 5),
    ("fed", 5),
    ("fii", 5),
    ("dii", 4),
    ("inflation", 5),
    ("cpi", 4),
    ("gdp", 4),
    ("earnings", 5),
    ("results", 4),
    ("quarterly", 4),
    ("nifty", 4),
    ("sensex", 4),
    ("nse", 3),
    ("bse", 3),
    ("crude", 4),
    ("oil", 3),
    ("rupee", 4),
    ("dollar", 3),
    ("tariff", 4),
    ("trade war", 5),
    ("rate cut", 5),
    ("rate hike", 5),
    ("repo rate", 5),
    ("ipo", 4),
    ("qip", 4),
    ("merger", 4),
    ("acquisition", 4),
    ("downgrade", 4),
    ("upgrade", 4),
    ("selloff", 4),
    ("rally", 3),
    ("china", 3),
    ("japan", 3),
    ("nasdaq", 3),
    ("s&p", 3),
    ("wall street", 3),
    ("geopolit", 4),
    ("war", 4),
    ("budget", 4),
    ("gst", 3),
    ("sebi", 3),
    ("bank", 3),
    ("it stocks", 3),
    ("auto", 2),
    ("pharma", 2),
    ("reliance", 3),
    ("hdfc", 3),
    ("tcs", 3),
    ("infosys", 3),
)


def score_headline_impact(title: str) -> int:
    """Higher score = more likely to move NSE / macro today."""
    t = title.lower()
    score = 0
    for kw, pts in _IMPACT_KEYWORDS:
        if kw in t:
            score += pts
    return score


def rank_headlines_by_impact(headlines: list[str], top_n: int = 10) -> list[tuple[str, int]]:
    scored = [(title, score_headline_impact(title)) for title in headlines]
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:top_n]


def build_24h_market_news_digest() -> NewsDigest:
    """Global + Indian headlines from the past ~24 hours for analyst briefing."""
    digest = NewsDigest()
    queries = [
        "India stock market news last 24 hours NSE BSE",
        "NSE stock specific news earnings results today",
        "RBI India economy inflation GDP macro news",
        "FII DII India market flow news today",
        "US Fed global markets Asia stock news today",
        "crude oil dollar rupee India market impact",
        "Nifty Sensex market news today India",
        "Wall Street global stock market news today",
    ]
    for q in queries:
        url = _GOOGLE_NEWS_RSS.format(query=quote_plus(q))
        for title in _fetch_rss(url, f"google:{q[:20]}", limit=8, max_age_hours=24):
            _tag_headline(title, digest)
            digest.sources.append("google_news")

    for title in fetch_yfinance_nifty_news(limit=10):
        _tag_headline(title, digest)
        digest.sources.append("yfinance")

    seen: set[str] = set()
    unique: list[str] = []
    for h in digest.headlines:
        key = h.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(h)
    digest.headlines = unique[:24]

    sc = digest.score()
    if sc >= 1.0:
        digest.news_bias = "bullish"
    elif sc <= -1.0:
        digest.news_bias = "bearish"
    else:
        digest.news_bias = "neutral"
    return digest


def build_market_news_digest() -> NewsDigest:
    """Aggregate headlines from yfinance + Google News RSS."""
    digest = NewsDigest()
    queries = [
        "Nifty 50 India stock market today",
        "Sensex BSE India stock market today",
        "India FII DII stock market",
        "global markets impact India",
    ]

    for title in fetch_yfinance_nifty_news():
        _tag_headline(title, digest)
        digest.sources.append("yfinance")

    for q in queries:
        url = _GOOGLE_NEWS_RSS.format(query=quote_plus(q))
        for title in _fetch_rss(url, f"google:{q[:20]}", limit=6):
            _tag_headline(title, digest)
            digest.sources.append("google_news")

    # De-dupe headlines
    seen: set[str] = set()
    unique: list[str] = []
    for h in digest.headlines:
        key = h.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(h)
    digest.headlines = unique[:18]

    sc = digest.score()
    if sc >= 1.0:
        digest.news_bias = "bullish"
    elif sc <= -1.0:
        digest.news_bias = "bearish"
    else:
        digest.news_bias = "neutral"
    return digest


def build_stock_news_digest(symbol: str) -> NewsDigest:
    """Headlines for a single NSE stock (yfinance + Google News RSS)."""
    digest = NewsDigest()
    ysym = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol

    try:
        for item in (yf.Ticker(ysym).news or [])[:6]:
            title = (item.get("title") or "").strip()
            if title:
                _tag_headline(title, digest)
                digest.sources.append("yfinance")
    except Exception:
        logger.debug("yfinance stock news unavailable for %s", symbol)

    for q in (f"{symbol} NSE stock India", f"{symbol} share news India"):
        url = _GOOGLE_NEWS_RSS.format(query=quote_plus(q))
        for title in _fetch_rss(url, f"google:{symbol}", limit=4):
            _tag_headline(title, digest)
            digest.sources.append("google_news")

    seen: set[str] = set()
    unique: list[str] = []
    for h in digest.headlines:
        key = h.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(h)
    digest.headlines = unique[:10]

    sc = digest.score()
    if sc >= 0.5:
        digest.news_bias = "bullish"
    elif sc <= -0.5:
        digest.news_bias = "bearish"
    else:
        digest.news_bias = "neutral"
    return digest


def classify_headlines(headlines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split headlines into positive, negative, and neutral buckets."""
    positive: list[str] = []
    negative: list[str] = []
    neutral: list[str] = []
    for text in headlines:
        text = (text or "").strip()
        if not text:
            continue
        bull = len(_BULLISH.findall(text))
        bear = len(_BEARISH.findall(text))
        if bull > bear:
            positive.append(text)
        elif bear > bull:
            negative.append(text)
        else:
            neutral.append(text)
    return positive, negative, neutral


def format_headlines_for_telegram(digest: NewsDigest, max_lines: int = 5) -> str:
    if not digest.headlines:
        return "No headlines fetched (check network)."
    lines = [f"• {h}" for h in digest.headlines[:max_lines]]
    if len(digest.headlines) > max_lines:
        lines.append(f"<i>+{len(digest.headlines) - max_lines} more headlines scanned</i>")
    return "\n".join(lines)
