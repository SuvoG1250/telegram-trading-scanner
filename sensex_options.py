"""Sensex index options — same SuperTrend + premium plan as Nifty."""

from __future__ import annotations

from config import SENSEX_OPTIONS_ENABLED, SENSEX_STRIKE_STEP, SENSEX_TICKER
from index_options import IndexOptionSpec, scan_index_supertrend_option
from option_quotes import fetch_sensex_option_quote

STRATEGY_NAME = "Sensex ST+TSL Options"

SENSEX_SPEC = IndexOptionSpec(
    key="sensex",
    label="SENSEX",
    strategy_name=STRATEGY_NAME,
    instrument="SENSEX_OPTION",
    yf_ticker=SENSEX_TICKER,
    strike_step=SENSEX_STRIKE_STEP,
    expiry_weekday=3,
    enabled=SENSEX_OPTIONS_ENABLED,
    fetch_quote=lambda strike, opt: fetch_sensex_option_quote(strike, opt),
)


def scan_sensex_supertrend_option():
    return scan_index_supertrend_option(SENSEX_SPEC)
