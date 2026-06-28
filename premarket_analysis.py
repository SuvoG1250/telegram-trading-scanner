"""Full pre-market update — overview, FII/DII, globals, index levels, stocks in focus."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from config import SEND_PREMARKET_FULL_ANALYSIS, UPSTOX_NIFTY_INSTRUMENT_KEY
from gemini_client import gemini_generate, llm_available
from index_btst import _fetch_gift_price, _option_chain_metrics
from market_news import build_market_news_digest, classify_headlines
from market_sentiment import assess_market_sentiment
from market_time import now_ist
from upstox_api import upstox_configured

logger = logging.getLogger(__name__)

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json",
}
_UPSTOX_BANK_KEY = "NSE_INDEX|Nifty Bank"

_GLOBAL_TICKERS = {
    "Nasdaq": ("^IXIC", "NQ=F"),
    "S&P 500": ("^GSPC", "ES=F"),
    "Dow Futures": ("YM=F", "^DJI"),
    "Hang Seng": ("^HSI",),
    "Kospi": ("^KS11",),
    "Nikkei": ("^N225",),
}

_NSE_INDEX_NAMES = {
    "NIFTY": "NIFTY 50",
    "BANK NIFTY": "NIFTY BANK",
    "SENSEX": None,
}

_INDEX_SPECS = {
    "NIFTY": {"ticker": "^NSEI", "step": 50, "upstox": UPSTOX_NIFTY_INSTRUMENT_KEY},
    "BANK NIFTY": {"ticker": "^NSEBANK", "step": 100, "upstox": _UPSTOX_BANK_KEY},
    "SENSEX": {"ticker": "^BSESN", "step": 100, "upstox": None},
}

_STOCK_FOCUS_RE = re.compile(
    r"\b([A-Z][A-Za-z&.\- ]{2,40})\b(?:\s*\([^)]+\))?\s*(?:\(|—|-)?\s*"
    r"(?:OFS|QIP|merger|partnership|fundraising|CFO|alliance|traffic|approval|update)",
    re.I,
)


@dataclass
class IndexSnapshot:
    label: str
    close: float
    prev_close: float
    day_high: float
    day_low: float
    change_pts: float
    supports: list[int] = field(default_factory=list)
    resistances: list[int] = field(default_factory=list)
    max_call_oi: int = 0
    max_put_oi: int = 0
    straddle_pts: float | None = None
    view_lines: list[str] = field(default_factory=list)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.rename(columns=str.capitalize)


def _round_level(value: float, step: int) -> int:
    return int(round(value / step) * step)


def _daily_ohlc(
    ticker: str,
    *,
    nse_name: str | None = None,
    nse_map: dict[str, dict[str, float]] | None = None,
) -> dict[str, float] | None:
    nse = nse_map if nse_map is not None else fetch_nse_index_map()
    yf_ohlc: dict[str, float] | None = None
    try:
        df = yf.Ticker(ticker).history(period="10d", interval="1d", auto_adjust=True)
        df = _normalize(df)
        if len(df) >= 2:
            row = df.iloc[-1]
            prev = df.iloc[-2]
            yf_ohlc = {
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "prev_close": float(prev["Close"]),
                "prev_high": float(prev["High"]),
                "prev_low": float(prev["Low"]),
            }
    except Exception:
        logger.debug("Daily OHLC failed for %s", ticker, exc_info=True)

    if nse_name and nse_name in nse:
        row = nse[nse_name]
        last = row["last"]
        base = yf_ohlc or {
            "open": last,
            "high": last,
            "low": last,
            "close": last,
            "prev_close": row["prev"],
            "prev_high": row["prev"],
            "prev_low": row["prev"],
        }
        base["close"] = last
        base["prev_close"] = row["prev"]
        return base
    return yf_ohlc


def _pivot_levels(high: float, low: float, close: float, step: int) -> tuple[list[int], list[int]]:
    pivot = (high + low + close) / 3.0
    r1 = 2 * pivot - low
    s1 = 2 * pivot - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)
    r3 = high + 2 * (pivot - low)
    s3 = low - 2 * (high - pivot)
    resistances = sorted(
        {_round_level(x, step) for x in (r1, r2, r3, high) if x > close * 0.999}
    )
    supports = sorted(
        {_round_level(x, step) for x in (s1, s2, s3, low) if x < close * 1.001},
        reverse=True,
    )
    if not supports:
        supports = [_round_level(close - step * i, step) for i in range(1, 7)]
    if not resistances:
        resistances = [_round_level(close + step * i, step) for i in range(1, 7)]
    while len(supports) < 6:
        supports.append(_round_level(supports[-1] - step, step))
    while len(resistances) < 6:
        resistances.append(_round_level(resistances[-1] + step, step))
    return supports[:6], resistances[:6]


def _global_points(label: str, tickers: tuple[str, ...]) -> float | None:
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(period="10d", interval="1d", auto_adjust=True)
            df = _normalize(df)
            if not df.empty:
                row = df.iloc[-1]
                session_pts = float(row["Close"]) - float(row["Open"])
                last = float(row["Close"])
                if abs(session_pts) <= last * 0.12:
                    return round(session_pts, 0)
            if len(df) >= 2:
                prev = float(df["Close"].iloc[-2])
                last = float(df["Close"].iloc[-1])
                pts = last - prev
                idx_gap = (df.index[-1] - df.index[-2]).days
                if idx_gap <= 3 and abs(pts) <= last * 0.12:
                    return round(pts, 0)
            df = yf.Ticker(ticker).history(period="2d", interval="30m", auto_adjust=True)
            df = _normalize(df)
            if len(df) >= 2:
                pts = float(df["Close"].iloc[-1]) - float(df["Open"].iloc[0])
                last = float(df["Close"].iloc[-1])
                if abs(pts) <= last * 0.12:
                    return round(pts, 0)
        except Exception:
            continue
    logger.debug("Global points unavailable for %s", label)
    return None


def _gift_nifty_points(nse_map: dict[str, dict[str, float]]) -> float | None:
    gift = _fetch_gift_price(("NIFTY1!", "SGXNifty=F"))
    spot = None
    if "NIFTY 50" in nse_map:
        spot = nse_map["NIFTY 50"]["last"]
    else:
        ohlc = _daily_ohlc("^NSEI")
        spot = ohlc["close"] if ohlc else None
    if gift and spot:
        return round(gift - spot, 0)
    return None


def fetch_nse_index_map() -> dict[str, dict[str, float]]:
    """NSE allIndices — last, previous close, change points."""
    out: dict[str, dict[str, float]] = {}
    try:
        session = requests.Session()
        session.headers.update(_NSE_HEADERS)
        session.get("https://www.nseindia.com", timeout=15)
        resp = session.get("https://www.nseindia.com/api/allIndices", timeout=15)
        resp.raise_for_status()
        for row in resp.json().get("data") or []:
            name = str(row.get("index") or "").strip()
            last = float(row.get("last") or 0)
            prev = float(row.get("previousClose") or 0)
            if name and last > 0 and prev > 0:
                out[name] = {
                    "last": last,
                    "prev": prev,
                    "change_pts": round(last - prev, 1),
                }
    except Exception:
        logger.warning("NSE index map fetch failed", exc_info=True)
    return out


def fetch_fii_dii() -> dict[str, str]:
    """Latest FII/DII cash provisional from NSE."""
    out: dict[str, str] = {}
    try:
        session = requests.Session()
        session.headers.update(_NSE_HEADERS)
        session.get("https://www.nseindia.com", timeout=15)
        resp = session.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=15)
        resp.raise_for_status()
        rows = resp.json()
        for row in rows:
            cat = str(row.get("category", "")).upper()
            net = float(row.get("netValue") or 0)
            date = str(row.get("date", ""))
            crore = f"{'+' if net >= 0 else ''}₹{abs(net):,.0f} Cr"
            if "FII" in cat or "FPI" in cat:
                out["fii_cash"] = crore
                out["fii_date"] = date
            elif cat == "DII":
                out["dii_cash"] = crore
                out["dii_date"] = date
    except Exception:
        logger.warning("FII/DII fetch failed", exc_info=True)
    return out


def _fetch_economic_events() -> list[str]:
    """High-level macro events today/tomorrow from news headlines."""
    news = build_market_news_digest()
    events: list[str] = []
    patterns = (
        r"\b(RBI|Fed|FOMC|BOJ|RBA|ECB|GDP|CPI|inflation|PMI|retail sales|"
        r"industrial production|housing starts|ZEW|interest rate|jobs data)\b"
    )
    for headline in news.headlines:
        if re.search(patterns, headline, re.I):
            events.append(headline[:100])
    if len(events) < 3:
        day = now_ist()
        defaults = [
            "US macro data & Fed speakers (check economic calendar)",
            "Asia: China/Japan/Australia data if scheduled",
            "Europe: sentiment & PMI releases if scheduled",
        ]
        if day.weekday() == 1:
            defaults.insert(0, "India: watch for weekly F&O expiry positioning")
        events.extend(defaults)
    seen: set[str] = set()
    unique: list[str] = []
    for e in events:
        key = e.lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[:8]


def _stocks_in_focus(headlines: list[str]) -> list[str]:
    picks: list[str] = []
    for h in headlines:
        if len(picks) >= 8:
            break
        if any(k in h.lower() for k in ("nifty", "sensex", "market", "fii", "dii", "global")):
            continue
        if re.search(r"\b(stock|shares|QIP|OFS|merger|partnership|CFO|alliance)\b", h, re.I):
            picks.append(h[:90])
    if len(picks) < 4:
        for h in headlines:
            sym = _STOCK_FOCUS_RE.search(h)
            if sym and h[:90] not in picks:
                picks.append(h[:90])
            if len(picks) >= 8:
                break
    return picks[:8]


def _index_snapshot(label: str, spec: dict[str, Any], nse_map: dict[str, dict[str, float]]) -> IndexSnapshot | None:
    nse_name = _NSE_INDEX_NAMES.get(label)
    ohlc = _daily_ohlc(spec["ticker"], nse_name=nse_name, nse_map=nse_map)
    if not ohlc:
        return None
    step = int(spec["step"])
    close = ohlc["close"]
    supports, resistances = _pivot_levels(ohlc["high"], ohlc["low"], close, step)
    snap = IndexSnapshot(
        label=label,
        close=round(close, 2),
        prev_close=round(ohlc["prev_close"], 2),
        day_high=round(ohlc["high"], 2),
        day_low=round(ohlc["low"], 2),
        change_pts=round(close - ohlc["prev_close"], 2),
        supports=supports,
        resistances=resistances,
    )
    upstox_key = spec.get("upstox")
    if upstox_key and upstox_configured():
        _, oi_note = _option_chain_metrics(upstox_key, close)
        m_call = re.search(r"Max Call OI (\d+)", oi_note)
        m_put = re.search(r"Max Put OI (\d+)", oi_note)
        if m_call:
            snap.max_call_oi = int(m_call.group(1))
        if m_put:
            snap.max_put_oi = int(m_put.group(1))
        atm = _round_level(close, step)
        try:
            from option_quotes import fetch_nifty_option_quote

            ce, _ = fetch_nifty_option_quote(atm, "CE")
            pe, _ = fetch_nifty_option_quote(atm, "PE")
            if ce and pe:
                snap.straddle_pts = round(ce.last_price + pe.last_price, 1)
        except Exception:
            pass
    if label == "NIFTY":
        snap.view_lines = [
            f"{_round_level(close * 1.002, step):,} এর উপরে: বুলিশ bias",
            f"Call writer resistance zone ~ {snap.max_call_oi or _round_level(close + step, step):,}",
            f"শক্ত Put writing ~ {snap.max_put_oi or _round_level(close - step, step):,}",
            "Expiry দিন: premium decay ও writer trap দেখুন",
        ]
    elif label == "BANK NIFTY":
        snap.view_lines = [
            f"{resistances[0] if resistances else _round_level(close * 1.005, step):,} এর উপরে: বুলিশ continuation",
            f"{int(ohlc['low']):,} এর নিচে: selling pressure বাড়তে পারে",
            "Sustainable rally-তে Nifty + Bank Nifty দুটোই support ধরে রাখা দরকার",
        ]
    else:
        snap.view_lines = [
            f"{resistances[0] if resistances else _round_level(close * 1.004, step):,} এর উপরে: positive momentum",
            f"{supports[0] if supports else _round_level(close * 0.996, step):,} এর নিচে: weakness বাড়তে পারে",
        ]
    return snap


def _session_day_name() -> str:
    return now_ist().strftime("%A").upper()


def _is_expiry_day() -> bool:
    """Nifty weekly expiry — Tuesday."""
    return now_ist().weekday() == 1


def _market_overview_lines(sentiment: dict, news_bias: str, gift_pts: float | None) -> list[str]:
    bias = sentiment.get("trade_bias", "neutral")
    gap = sentiment.get("nifty_gap_pct", 0.0)
    lines: list[str] = []
    if gap > 0.4:
        lines.append("গতকাল বাজার gap-up opening দিয়েছিল, সেশনে selective profit booking দেখা গেছে।")
    elif gap < -0.4:
        lines.append("গতকাল বাজার দুর্বল close — global cue ও FII flow-এ focus রাখুন।")
    else:
        lines.append("গতকালের সেশন range-bound / mixed — breakout-এর জন্য confirmation জরুরি।")
    if _is_expiry_day():
        lines.append("আজ Nifty expiry — option positioning, premium decay ও writers-এর behaviour গুরুত্বপূর্ণ।")
    if gift_pts is not None:
        if gift_pts > 20:
            lines.append(f"Gift Nifty এখন ~{gift_pts:+.0f} pts premium-এ — positive opening bias।")
        elif gift_pts < -20:
            lines.append(f"Gift Nifty ~{abs(gift_pts):.0f} pts discount-এ — cautious open সম্ভব।")
    if news_bias == "bearish":
        lines.append("Headline flow একটু risk-off — position size ছোট রাখুন।")
    elif news_bias == "bullish" and bias == "bullish":
        lines.append("Global + domestic sentiment align — trend side-কে priority দিন।")
    return lines[:4]


def gather_premarket_data() -> dict[str, Any]:
    sentiment = assess_market_sentiment()
    news = build_market_news_digest()
    positive, negative, _neutral = classify_headlines(news.headlines)
    fii = fetch_fii_dii()
    nse_map = fetch_nse_index_map()
    gift_pts = _gift_nifty_points(nse_map)

    globals_pts: dict[str, float | None] = {}
    if gift_pts is not None:
        globals_pts["Gift Nifty"] = gift_pts
    for label, tickers in _GLOBAL_TICKERS.items():
        globals_pts[label] = _global_points(label, tickers)

    indices: dict[str, IndexSnapshot] = {}
    for key, spec in _INDEX_SPECS.items():
        snap = _index_snapshot(key, spec, nse_map)
        if snap:
            indices[key] = snap

    return {
        "sentiment": sentiment,
        "news_bias": news.news_bias,
        "positive": positive,
        "negative": negative,
        "fii": fii,
        "gift_pts": gift_pts,
        "overview": _market_overview_lines(sentiment, news.news_bias, gift_pts),
        "economic": _fetch_economic_events(),
        "stocks_focus": _stocks_in_focus(positive + news.headlines),
        "globals": globals_pts,
        "indices": indices,
    }


def _fmt_pts(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.0f} Points"


def format_premarket_update(data: dict[str, Any] | None = None) -> str:
    data = data or gather_premarket_data()
    day = _session_day_name()
    date_label = now_ist().strftime("%d %B").upper()
    ts = now_ist().strftime("%H:%M IST")

    lines = [
        f"<b>প্রি-মার্কেট আপডেট | {day}, {date_label}</b>",
        f"<i>{ts}</i>",
        "",
        "<b>বাজার সারাংশ</b>",
    ]
    for item in data.get("overview") or ["আজকের সেশনের জন্য global ও domestic cue scan করা হচ্ছে।"]:
        lines.append(f"• {item}")

    fii = data.get("fii") or {}
    lines.extend(["", "<b>FII ও DII ডেটা</b>"])
    if fii.get("fii_cash") or fii.get("dii_cash"):
        fii_line = f"• FII Cash: {fii.get('fii_cash', 'n/a')} | DII Cash: {fii.get('dii_cash', 'n/a')}"
        if fii.get("fii_date"):
            fii_line += f" <i>({fii['fii_date']})</i>"
        lines.append(fii_line)
        lines.append("• FII Futures: Bank Nifty / Nifty / Midcap bias-এর জন্য NSE participant data দেখুন")
    else:
        lines.append("• FII/DII provisional data unavailable — NSE India website চেক করুন")

    lines.extend(["", "<b>ইকোনমিক ক্যালেন্ডার</b>"])
    for ev in data.get("economic") or ["Major event parse হয়নি — Investing.com calendar verify করুন"]:
        lines.append(f"• {ev}")

    lines.extend(["", "<b>ফোকাস স্টক</b>"])
    stocks = data.get("stocks_focus") or []
    if stocks:
        for s in stocks:
            lines.append(f"• {s}")
    else:
        lines.append("• feed-এ single-stock catalyst headline নেই — Nifty heavyweights দেখুন")

    lines.extend(["", "<b>গ্লোবাল বাজার</b>"])
    for label, pts in (data.get("globals") or {}).items():
        lines.append(f"• {label}: {_fmt_pts(pts)}")

    for key in ("NIFTY", "BANK NIFTY", "SENSEX"):
        snap: IndexSnapshot | None = (data.get("indices") or {}).get(key)
        if not snap:
            continue
        title = f"{key} লেভেল"
        lines.extend(["", f"<b>{title}</b>", f"• Close: {snap.close:,.0f}"])
        if key == "NIFTY" and snap.straddle_pts:
            atm = _round_level(snap.close, 50)
            lines.append(f"• {atm:,} Straddle: ~{snap.straddle_pts:.0f} Points")
        if snap.max_call_oi:
            lines.append(f"• Highest Call Writing: {snap.max_call_oi:,}")
        if snap.max_put_oi:
            lines.append(f"• Strongest Put Writing: {snap.max_put_oi:,}")
        if snap.max_call_oi and snap.max_put_oi:
            lines.append(f"• Key Range: {min(snap.max_put_oi, snap.max_call_oi):,} - {max(snap.max_put_oi, snap.max_call_oi):,}")
        if snap.supports:
            lines.append(f"Support: {' | '.join(f'{x:,}' for x in snap.supports)}")
        if snap.resistances:
            lines.append(f"Resistance: {' | '.join(f'{x:,}' for x in snap.resistances)}")
        lines.append("View:")
        for v in snap.view_lines:
            lines.append(f"• {v}")

    pos = data.get("positive") or []
    neg = data.get("negative") or []
    lines.extend(["", "<b>সামগ্রিক দৃষ্টিভঙ্গি</b>"])
    global_pos = sum(1 for v in (data.get("globals") or {}).values() if v is not None and v > 0)
    if global_pos >= 4:
        lines.append("• Global sentiment বেশিরভাগ ইতিবাচক।")
    elif global_pos <= 1:
        lines.append("• Global cue mixed-to-negative — gap-down risk monitor করুন।")
    else:
        lines.append("• Global markets mixed — stock-specific action বেশি হতে পারে।")
    if neg:
        lines.append(f"• Headline risk: {neg[0][:80]}")
    if pos:
        lines.append(f"• Positive cue: {pos[0][:80]}")
    nifty = (data.get("indices") or {}).get("NIFTY")
    if nifty and nifty.supports and nifty.resistances:
        lines.append(
            f"• Nifty-র জন্য {nifty.supports[0]:,} - {nifty.resistances[-1]:,} গুরুত্বপূর্ণ zone থাকবে।"
        )
    if _is_expiry_day():
        lines.append("• Expiry দিন — patience ও confirmation-based trading-এ focus রাখুন।")
    else:
        lines.append("• Opening range clear হওয়ার পর aggressive entry নেওয়া ভালো।")

    return "\n".join(lines)


def _llm_polish(raw_text: str, data: dict[str, Any]) -> str:
    if not llm_available():
        return raw_text
    prompt = (
        "You are an expert Indian stock market pre-market analyst. Rewrite the following Telegram HTML "
        "pre-market update in SIMPLE, CLEAR BENGALI (Bangla Unicode script — NOT romanized Bengali, NOT Hinglish). "
        "Keep ALL numbers, index levels, and stock names exactly as given. "
        "Keep the same structure and sections. Keep HTML tags (<b>, <i>). Do not add new sections. Max 4500 chars.\n\n"
        f"DATA JSON (reference):\n{data.get('news_bias')} bias\n\n"
        f"DRAFT:\n{raw_text}"
    )
    polished = gemini_generate(prompt, max_tokens=1800, temperature=0.35)
    if polished and len(polished) > 400:
        return polished
    return raw_text


def format_full_premarket_analysis(*, use_llm: bool = True) -> str:
    if not SEND_PREMARKET_FULL_ANALYSIS:
        from premarket_summary import format_premarket_market_summary

        return format_premarket_market_summary()
    data = gather_premarket_data()
    text = format_premarket_update(data)
    if use_llm:
        text = _llm_polish(text, data)
    return text
