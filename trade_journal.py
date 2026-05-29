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


def _trade_label(trade: TradeRecord) -> str:
    if trade.instrument == "NIFTY_OPTION":
        strike = int(trade.strike or 0)
        opt = trade.option_type or ""
        return f"NIFTY {strike} {opt} ({trade.side})"
    side = "SHORT SELL" if trade.side == "SELL" else trade.side
    return f"{trade.symbol} ({side})"


def _format_trade_row(trade: TradeRecord, outcome: Outcome, pnl: float) -> str:
    tag = _trade_label(trade)
    strat = trade.strategy or "—"
    result = "Profit" if outcome == "WIN" else ("Loss" if outcome == "LOSS" else "Flat")
    return (
        f"• <b>{tag}</b> · <i>{strat}</i>\n"
        f"  Entry Rs {trade.entry:,.2f} → <b>{'+' if pnl >= 0 else ''}{pnl:.2f}%</b> ({result})"
    )


def format_daily_summary() -> str:
    trades = load_today_trades()
    date_label = now_ist().strftime("%d %b %Y")
    close_time = now_ist().strftime("%H:%M IST")
    lines = [
        f"📊 <b>NSE Indian Market — EOD P/L Summary</b> — {date_label}",
        f"<i>After 3:30 PM IST market close · {close_time}</i>",
        "",
    ]
    if not trades:
        lines.extend(
            [
                "No BUY / SHORT SELL / Nifty signals were sent today.",
                "",
                "<b>Net P/L:</b> 0.00% · 0 trades",
            ]
        )
        return "\n".join(lines)

    equity_win: list[str] = []
    equity_loss: list[str] = []
    equity_flat: list[str] = []
    opt_win: list[str] = []
    opt_loss: list[str] = []
    opt_flat: list[str] = []
    pnl_values: list[float] = []
    win_pnls: list[float] = []
    loss_pnls: list[float] = []

    for t in trades:
        outcome, pnl = evaluate_trade(t)
        pnl_values.append(pnl)
        row = _format_trade_row(t, outcome, pnl)
        is_opt = t.instrument == "NIFTY_OPTION"
        if outcome == "WIN":
            win_pnls.append(pnl)
            (opt_win if is_opt else equity_win).append(row)
        elif outcome == "LOSS":
            loss_pnls.append(pnl)
            (opt_loss if is_opt else equity_loss).append(row)
        else:
            (opt_flat if is_opt else equity_flat).append(row)

    net_pnl = sum(pnl_values)
    win_sum = sum(win_pnls)
    loss_sum = sum(loss_pnls)
    n_win = len(win_pnls)
    n_loss = len(loss_pnls)
    n_flat = len(equity_flat) + len(opt_flat)

    lines.extend(
        [
            f"💰 <b>Net P/L (day):</b> {'+' if net_pnl >= 0 else ''}{net_pnl:.2f}%",
            f"✅ <b>Total profit:</b> +{win_sum:.2f}% across {n_win} trade(s)",
            f"❌ <b>Total loss:</b> {loss_sum:.2f}% across {n_loss} trade(s)",
            "",
            f"<b>Stocks</b> — ✅ {len(equity_win)} · ❌ {len(equity_loss)} · ➖ {len(equity_flat)}",
        ]
    )
    if equity_win:
        lines.append("<b>Profit</b>")
        lines.extend(equity_win)
    if equity_loss:
        lines.append("<b>Loss</b>")
        lines.extend(equity_loss)
    if equity_flat:
        lines.append("<b>Flat</b>")
        lines.extend(equity_flat)
    if not (equity_win or equity_loss or equity_flat):
        lines.append("• — no stock signals today —")

    n_opt = len(opt_win) + len(opt_loss) + len(opt_flat)
    if n_opt:
        lines.extend(
            [
                "",
                f"<b>Nifty options</b> — ✅ {len(opt_win)} · ❌ {len(opt_loss)} · ➖ {len(opt_flat)}",
            ]
        )
        if opt_win:
            lines.append("<b>Profit</b>")
            lines.extend(opt_win)
        if opt_loss:
            lines.append("<b>Loss</b>")
            lines.extend(opt_loss)
        if opt_flat:
            lines.append("<b>Flat</b>")
            lines.extend(opt_flat)

    lines.extend(
        [
            "",
            f"<b>Day total:</b> {len(trades)} signals · "
            f"✅ {n_win} · ❌ {n_loss} · ➖ {n_flat}",
            "<i>P/L % vs entry using session high/low/close (not live fills).</i>",
        ]
    )
    try:
        from ai_improvements import build_daily_ai_insight

        insight = build_daily_ai_insight(len(trades), net_pnl, n_win, n_loss)
        if insight:
            lines.extend(["", f"🤖 <b>AI day note:</b> <i>{insight}</i>"])
    except Exception:
        logger.debug("Daily AI insight skipped.", exc_info=True)
    return "\n".join(lines)


def send_daily_summary() -> bool:
    text = format_daily_summary()
    if send_plain(text):
        logger.info("Daily summary sent (%s trades).", len(load_today_trades()))
        return True
    logger.error("Failed to send daily summary.")
    return False
