"""Nifty 50 BTST — probability gap model (3:15–3:25 PM IST)."""

from __future__ import annotations

from config import NIFTY_BTST_ENABLED, NIFTY_STRIKE_STEP, UPSTOX_NIFTY_INSTRUMENT_KEY, upstox_nifty_lot_size
from index_btst import IndexBtstSpec, run_index_btst_alert
from market_sentiment import NIFTY_TICKER
from option_quotes import fetch_nifty_option_quote
from state import mark_nifty_btst_sent, nifty_btst_sent
from telegram_client import Signal

NIFTY_BTST_SPEC = IndexBtstSpec(
    key="nifty",
    label="NIFTY",
    yf_ticker=NIFTY_TICKER,
    gift_tickers=("NIFTY1!", "SGXNifty=F"),
    instrument="NIFTY_OPTION",
    upstox_key=UPSTOX_NIFTY_INSTRUMENT_KEY,
    strike_step=NIFTY_STRIKE_STEP,
    expiry_weekday=1,
    lot_size=upstox_nifty_lot_size(),
    fetch_quote=lambda strike, opt: fetch_nifty_option_quote(strike, opt),
    sent_check=nifty_btst_sent,
    mark_sent=mark_nifty_btst_sent,
    strategy_name="Nifty Gap Probability BTST",
)


def run_nifty_btst_alert(*, force: bool = False) -> Signal | None:
    if not NIFTY_BTST_ENABLED:
        return None
    return run_index_btst_alert(NIFTY_BTST_SPEC, force=force)
