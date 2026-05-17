"""Market sentiment proxy (Nifty gap + global indices). FII/DII not available on free APIs."""

from __future__ import annotations

import logging

import yfinance as yf

logger = logging.getLogger(__name__)

NIFTY_TICKER = "^NSEI"
GLOBAL_TICKERS = {"US": "^GSPC", "NASDAQ": "^IXIC"}


def _last_two_closes(ticker: str) -> tuple[float, float] | None:
    try:
        df = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
        if df is None or len(df) < 2:
            return None
        prev = float(df["Close"].iloc[-2])
        last = float(df["Close"].iloc[-1])
        return prev, last
    except Exception:
        logger.exception("Sentiment fetch failed for %s", ticker)
        return None


def assess_market_sentiment() -> dict:
    """
    Proxy for pre-trade sentiment:
    - Nifty gap up / gap down vs prior close
    - Global indices direction (prior session)
  FII/DII: check NSE / moneycontrol manually (no free API).
    """
    result: dict = {
        "nifty_gap": "flat",
        "nifty_gap_pct": 0.0,
        "global": "mixed",
        "summary": "",
        "trade_bias": "neutral",
    }

    nifty = _last_two_closes(NIFTY_TICKER)
    if nifty:
        prev, last = nifty
        gap_pct = ((last - prev) / prev) * 100 if prev else 0
        result["nifty_gap_pct"] = round(gap_pct, 2)
        if gap_pct > 0.3:
            result["nifty_gap"] = "gap_up"
        elif gap_pct < -0.3:
            result["nifty_gap"] = "gap_down"
        else:
            result["nifty_gap"] = "flat"

    global_up = 0
    global_down = 0
    for name, ticker in GLOBAL_TICKERS.items():
        pair = _last_two_closes(ticker)
        if not pair:
            continue
        prev, last = pair
        if last > prev:
            global_up += 1
        elif last < prev:
            global_down += 1

    if global_up >= 2:
        result["global"] = "positive"
    elif global_down >= 2:
        result["global"] = "negative"
    else:
        result["global"] = "mixed"

    if result["nifty_gap"] == "gap_up" and result["global"] != "negative":
        result["trade_bias"] = "bullish"
    elif result["nifty_gap"] == "gap_down" and result["global"] != "positive":
        result["trade_bias"] = "bearish"
    else:
        result["trade_bias"] = "neutral"

    result["summary"] = (
        f"Nifty: {result['nifty_gap']} ({result['nifty_gap_pct']:+.2f}%) | "
        f"Global: {result['global']} | Bias: {result['trade_bias']}\n"
        "_(FII/DII: verify manually on NSE — not in free feed)_"
    )
    return result


def format_sentiment_block() -> str:
    s = assess_market_sentiment()
    emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(s["trade_bias"], "⚪")
    return f"{emoji} Market Sentiment\n{s['summary']}"
