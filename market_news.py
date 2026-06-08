"""Free market headlines — yfinance + Google News RSS (no paid API)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import yfinance as yf

from market_sentiment import NIFTY_TICKER

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


def _fetch_rss(url: str, source: str, limit: int = 12) -> list[str]:
    titles: list[str] = []
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; NSE-Scanner/1.0)"})
        with urlopen(req, timeout=20) as resp:
            root = ET.fromstring(resp.read())
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
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


def build_market_news_digest() -> NewsDigest:
    """Aggregate headlines from yfinance + Google News RSS."""
    digest = NewsDigest()
    queries = [
        "Nifty 50 India stock market today",
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


def format_headlines_for_telegram(digest: NewsDigest, max_lines: int = 5) -> str:
    if not digest.headlines:
        return "No headlines fetched (check network)."
    lines = [f"• {h}" for h in digest.headlines[:max_lines]]
    if len(digest.headlines) > max_lines:
        lines.append(f"<i>+{len(digest.headlines) - max_lines} more headlines scanned</i>")
    return "\n".join(lines)
