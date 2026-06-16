"""Sensex BTST — probability gap model (3:15–3:25 PM IST)."""

from __future__ import annotations

from config import (
    SENSEX_BTST_ENABLED,
    SENSEX_STRIKE_STEP,
    SENSEX_TICKER,
    UPSTOX_SENSEX_INSTRUMENT_KEY,
    upstox_sensex_lot_size,
)
from index_btst import IndexBtstSpec, run_index_btst_alert
from option_quotes import fetch_sensex_option_quote
from state import mark_sensex_btst_sent, sensex_btst_sent
from telegram_client import Signal

SENSEX_BTST_SPEC = IndexBtstSpec(
    key="sensex",
    label="SENSEX",
    yf_ticker=SENSEX_TICKER,
    gift_tickers=("NIFTY1!", "SGXNifty=F"),
    instrument="SENSEX_OPTION",
    upstox_key=UPSTOX_SENSEX_INSTRUMENT_KEY,
    strike_step=SENSEX_STRIKE_STEP,
    expiry_weekday=3,
    lot_size=upstox_sensex_lot_size(),
    fetch_quote=lambda strike, opt: fetch_sensex_option_quote(strike, opt),
    sent_check=sensex_btst_sent,
    mark_sent=mark_sensex_btst_sent,
    strategy_name="Sensex Gap Probability BTST",
)


def run_sensex_btst_alert(*, force: bool = False) -> Signal | None:
    if not SENSEX_BTST_ENABLED:
        return None
    return run_index_btst_alert(SENSEX_BTST_SPEC, force=force)
