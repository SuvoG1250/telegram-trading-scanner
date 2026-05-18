"""Log sent trades and build end-of-day win/loss summary."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

import yfinance as yf

from config import DATA_DIR, TRADES_JOURNAL_FILE
from market_sentiment import NIFTY_TICKER
from market_time import IST, now_ist, today_key
from stocks import to_yfinance_symbol
from telegram_client import Signal, send_plain

logger = logging.getLogger(__name__)

Outcome = Literal["WIN", "LOSS", "FLAT"]


@dataclass
class TradeRecord:
    symbol: str
    side: str
    strategy: str
    instrument: str
    entry: float
    stop_loss: float
    target: float
    sent_at: str
    strike: float | None = None
    option_type: str | None = None
    underlying: float | None = None
    underlying_sl: float | None = None
    underlying_target: float | None = None


def _load_journal() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_JOURNAL_FILE.exists():
        return {"date": today_key(), "trades": []}
    try:
        data = json.loads(TRADES_JOURNAL_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": today_key(), "trades": []}
    if data.get("date") != today_key():
        return {"date": today_key(), "trades": []}
    return data


def _save_journal(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRADES_JOURNAL_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record_trade(signal: Signal) -> None:
    lv = signal.levels
    rec = TradeRecord(
        symbol=signal.symbol,
        side=signal.side,
        strategy=signal.strategy,
        instrument=signal.instrument,
        entry=lv.entry,
        stop_loss=lv.stop_loss,
        target=lv.primary_target,
        sent_at=now_ist().isoformat(),
        strike=signal.strike,
        option_type=signal.option_type,
        underlying=signal.underlying,
        underlying_sl=signal.underlying_sl,
        underlying_target=signal.underlying_target,
    )
    data = _load_journal()
    data["trades"].append(asdict(rec))
    _save_journal(data)
    logger.info("Journal: recorded %s %s", signal.symbol, signal.side)


def load_today_trades() -> list[TradeRecord]:
    return [TradeRecord(**t) for t in _load_journal().get("trades", [])]


def _session_bars(symbol: str, sent_at: datetime) -> tuple[float, float, float] | None:
    """Day high, low, last close since signal (5m bars)."""
    ticker = NIFTY_TICKER if symbol.startswith("NIFTY") else to_yfinance_symbol(symbol.split()[0])
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="5m", auto_adjust=True)
    except Exception:
        logger.exception("Journal fetch failed: %s", symbol)
        return None
    if df.empty:
        return None
    if isinstance(df.columns, type(df.columns)) and hasattr(df.columns, "levels"):
        if getattr(df.columns, "nlevels", 1) > 1:
            df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.capitalize)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    local = df.index.tz_convert(IST)
    sent_utc = sent_at.astimezone(IST)
    mask = local >= sent_utc.replace(second=0, microsecond=0)
    sub = df.loc[mask]
    if sub.empty:
        sub = df
    high = float(sub["High"].max())
    low = float(sub["Low"].min())
    close = float(sub["Close"].iloc[-1])
    return high, low, close


def _classify_long(entry: float, sl: float, target: float, high: float, low: float, close: float) -> tuple[Outcome, float]:
    pnl_pct = (close - entry) / entry * 100 if entry else 0.0
    if high >= target:
        return "WIN", round((target - entry) / entry * 100, 2)
    if low <= sl:
        return "LOSS", round((sl - entry) / entry * 100, 2)
    if close > entry:
        return "WIN", round(pnl_pct, 2)
    if close < entry:
        return "LOSS", round(pnl_pct, 2)
    return "FLAT", 0.0


def _classify_short(entry: float, sl: float, target: float, high: float, low: float, close: float) -> tuple[Outcome, float]:
    pnl_pct = (entry - close) / entry * 100 if entry else 0.0
    if low <= target:
        return "WIN", round((entry - target) / entry * 100, 2)
    if high >= sl:
        return "LOSS", round((entry - sl) / entry * 100, 2)
    if close < entry:
        return "WIN", round(pnl_pct, 2)
    if close > entry:
        return "LOSS", round(-pnl_pct, 2)
    return "FLAT", 0.0


def evaluate_trade(trade: TradeRecord) -> tuple[Outcome, float]:
    try:
        sent_at = datetime.fromisoformat(trade.sent_at)
        if sent_at.tzinfo is None:
            sent_at = IST.localize(sent_at)
    except (TypeError, ValueError):
        sent_at = now_ist()

    if trade.instrument == "NIFTY_OPTION" and trade.underlying is not None:
        bars = _session_bars("NIFTY", sent_at)
        if bars:
            high, low, close = bars
            u_entry = trade.underlying
            u_sl = trade.underlying_sl or trade.stop_loss
            u_tgt = trade.underlying_target or trade.target
            if "CALL" in trade.side:
                return _classify_long(u_entry, u_sl, u_tgt, high, low, close)
            return _classify_short(u_entry, u_sl, u_tgt, high, low, close)

    bars = _session_bars(trade.symbol, sent_at)
    if not bars:
        return "FLAT", 0.0
    high, low, close = bars
    if trade.side == "BUY":
        return _classify_long(trade.entry, trade.stop_loss, trade.target, high, low, close)
    if trade.side == "SELL":
        return _classify_short(trade.entry, trade.stop_loss, trade.target, high, low, close)
    if "CALL" in trade.side:
        return _classify_long(trade.entry, trade.stop_loss, trade.target, high, low, close)
    return _classify_short(trade.entry, trade.stop_loss, trade.target, high, low, close)


def format_daily_summary() -> str:
    trades = load_today_trades()
    date_label = now_ist().strftime("%d %b %Y")
    lines = [
        f"📊 <b>Full Day Summary</b> — {date_label}",
        f"<i>All signals sent today · evaluated at market close</i>",
        "",
    ]
    if not trades:
        lines.append("No trades were sent today.")
        return "\n".join(lines)

    winners: list[str] = []
    losers: list[str] = []
    flat: list[str] = []

    for t in trades:
        outcome, pnl = evaluate_trade(t)
        tag = t.symbol if t.instrument == "EQUITY" else f"{t.symbol} ({t.side})"
        row = (
            f"• <b>{tag}</b> @ ₹{t.entry:,.2f} → "
            f"{'+' if pnl >= 0 else ''}{pnl:.2f}%"
        )
        if outcome == "WIN":
            winners.append(row)
        elif outcome == "LOSS":
            losers.append(row)
        else:
            flat.append(row)

    lines.append(f"✅ <b>Profited ({len(winners)})</b>")
    lines.extend(winners if winners else ["• — none —"])
    lines.append("")
    lines.append(f"❌ <b>Loss ({len(losers)})</b>")
    lines.extend(losers if losers else ["• — none —"])
    if flat:
        lines.append("")
        lines.append(f"➖ <b>Flat / open ({len(flat)})</b>")
        lines.extend(flat)
    lines.extend(
        [
            "",
            f"<b>Total:</b> {len(trades)} signals · "
            f"✅ {len(winners)} · ❌ {len(losers)} · ➖ {len(flat)}",
            "<i>New trades blocked after 3:00 PM IST.</i>",
        ]
    )
    return "\n".join(lines)


def send_daily_summary() -> bool:
    text = format_daily_summary()
    if send_plain(text):
        logger.info("Daily summary sent (%s trades).", len(load_today_trades()))
        return True
    logger.error("Failed to send daily summary.")
    return False
