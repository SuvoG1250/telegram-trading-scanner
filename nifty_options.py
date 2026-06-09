"""
Nifty index options — SuperTrend flip → Buy CE/PE.

Engines:
  • tv — TradingView-style ta.supertrend (ATR length / factor on NIFTY_ST_*).
  • exit490 — Pine v4 "SuperTrend ATR with TSL" (exit490): ATR(bars)×mult on hl2, trailing bands.

Live premium: Fyers → Upstox → Dhan → estimate (see OPTION_DATA_PROVIDER).

Premium plan (default): SL ₹15, book +₹30, runner trail zone up to +₹100 from entry.
"""

from __future__ import annotations

from config import NIFTY_OPTIONS_ENABLED, NIFTY_STRIKE_STEP
from index_options import IndexOptionSpec, scan_index_supertrend_option
from market_sentiment import NIFTY_TICKER
from option_quotes import fetch_nifty_option_quote

STRATEGY_NAME = "Nifty ST+TSL Options"

NIFTY_SPEC = IndexOptionSpec(
    key="nifty",
    label="NIFTY",
    strategy_name=STRATEGY_NAME,
    instrument="NIFTY_OPTION",
    yf_ticker=NIFTY_TICKER,
    strike_step=NIFTY_STRIKE_STEP,
    expiry_weekday=1,
    enabled=NIFTY_OPTIONS_ENABLED,
    fetch_quote=lambda strike, opt: fetch_nifty_option_quote(strike, opt),
)


def scan_nifty_supertrend_option():
    return scan_index_supertrend_option(NIFTY_SPEC)
