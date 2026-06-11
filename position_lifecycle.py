"""
Track equity and Nifty option recommendations until SL or target is touched (delayed LTP).
After exit, defer new Telegram picks for same symbol/premium slot until reopened; flag re-entry.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from config import ACTIVE_POSITIONS_FILE, DATA_DIR
from market_time import today_key
from telegram_client import Signal

logger = logging.getLogger(__name__)

ExitReason = Literal["TARGET_HIT", "STOP_LOSS"]


def caption_after_prior_exit(reason: ExitReason | None, *, basket: Literal["equity", "option"]) -> str:
    """Plain-text footer shown on Telegram re-entry prompts."""
    if reason is None:
        return ""
    if reason == "TARGET_HIT":
        return (
            "Flag: fresh equity signal after TARGET was touched on earlier plan today."
            if basket == "equity"
            else "Flag: fresh premium signal after TARGET was touched on earlier plan today."
        )
    return (
        "Flag: fresh equity signal after STOP LOSS was touched on earlier plan today."
        if basket == "equity"
        else "Flag: fresh premium signal after STOP LOSS was touched on earlier plan today."
    )


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> dict[str, Any]:
    _ensure_file()
    if not ACTIVE_POSITIONS_FILE.exists():
        return _empty_blob()
    try:
        blob = json.loads(ACTIVE_POSITIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not read active_positions — resetting.")
        return _empty_blob()
    if blob.get("date") != today_key():
        return _empty_blob()
    blob.setdefault("equity", [])
    blob.setdefault("premium", [])
    blob.setdefault("global", [])
    blob.setdefault("reentry_stock", {})
    blob.setdefault("reentry_option", {})
    blob.setdefault("equity_buy_sent_today", 0)
    blob.setdefault("date", today_key())
    return blob


def _save(blob: dict[str, Any]) -> None:
    _ensure_file()
    ACTIVE_POSITIONS_FILE.write_text(json.dumps(blob, indent=2), encoding="utf-8")


def _empty_blob() -> dict[str, Any]:
    return {
        "date": today_key(),
        "equity": [],
        "premium": [],
        "global": [],
        "reentry_stock": {},
        "reentry_option": {},
        "equity_buy_sent_today": 0,
    }


_GLOBAL_TICKERS: dict[str, str] = {
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "XAUUSD": "XAUUSD=X",
}


def _last_close_equity_px(symbol: str) -> float | None:
    from data_fetcher import fetch_history, today_session_df
    from market_time import now_ist

    hist = fetch_history(symbol, "5m", period="5d")
    if hist.empty:
        return None
    sess = today_session_df(hist, now_ist().date())
    if sess.empty or "Close" not in sess.columns:
        return None
    try:
        return float(sess["Close"].iloc[-1])
    except (TypeError, ValueError):
        return None


def _resolve_equity_row(row: dict[str, Any], px: float) -> ExitReason | None:
    side = row.get("side") or "BUY"
    sl = float(row["stop_loss"])
    tp = float(row["target"])
    if side == "BUY":
        if px <= sl:
            return "STOP_LOSS"
        if px >= tp:
            return "TARGET_HIT"
    else:  # SELL
        if px >= sl:
            return "STOP_LOSS"
        if px <= tp:
            return "TARGET_HIT"
    return None


def reconcile_equity_positions() -> list[tuple[str, str, ExitReason]]:
    """Update OPEN equity trades; tag re-entry banners; return closures for optional Telegram."""
    blob = _load()
    closed: list[tuple[str, str, ExitReason]] = []
    reentry_stock: dict[str, str] = blob.get("reentry_stock") or {}
    touched = False

    for row in blob["equity"]:
        if row.get("status") != "OPEN":
            continue
        sym = row["symbol"]
        strat = row.get("strategy") or ""
        px = _last_close_equity_px(sym)
        if px is None:
            continue
        reason = _resolve_equity_row(row, px)
        if reason is None:
            continue
        row["status"] = "CLOSED"
        row["exit_reason"] = reason
        reentry_stock[sym] = reason
        closed.append((sym, strat, reason))
        touched = True

    if touched:
        blob["reentry_stock"] = reentry_stock
        _save(blob)
    return closed


def reconcile_premium_positions() -> list[tuple[str, ExitReason]]:
    """Resolve Nifty premium legs using OPTION_DATA_PROVIDER chain."""
    blob = _load()
    closed: list[tuple[str, ExitReason]] = []
    reentry_opt: dict[str, str] = blob.get("reentry_option") or {}
    touched = False

    for row in blob["premium"]:
        if row.get("status") != "OPEN":
            continue
        strike = int(row["strike"])
        opt = str(row.get("option_type") or row.get("opt") or "CE").upper()
        if opt not in ("CE", "PE"):
            opt = "CE"
        instrument = str(row.get("instrument") or "NIFTY_OPTION")
        side_key = _option_slot_key(
            instrument,
            row.get("side_label") or f"BUY {'CALL' if opt == 'CE' else 'PUT'}",
        )
        if instrument == "SENSEX_OPTION":
            from option_quotes import fetch_sensex_option_quote

            q, _src = fetch_sensex_option_quote(strike, opt)
        else:
            from option_quotes import fetch_nifty_option_quote

            q, _src = fetch_nifty_option_quote(strike, opt)
        if q is None or q.last_price <= 0:
            continue
        ltp = float(q.last_price)
        sl = float(row["stop_loss"])
        tp = float(row["target"])
        if ltp <= sl:
            reason: ExitReason = "STOP_LOSS"
        elif ltp >= tp:
            reason = "TARGET_HIT"
        else:
            continue

        row["status"] = "CLOSED"
        row["exit_reason"] = reason
        row["exit_ltp"] = round(ltp, 4)
        reentry_opt[side_key] = reason
        closed.append((side_key, reason))
        touched = True

    if touched:
        blob["reentry_option"] = reentry_opt
        _save(blob)
    return closed


def _last_close_global_px(symbol: str) -> float | None:
    ticker = _GLOBAL_TICKERS.get(symbol.upper())
    if not ticker:
        return None
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="5d", interval="1h", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except (TypeError, ValueError):
        return None
    except Exception:
        logger.debug("Global LTP fetch failed for %s", symbol, exc_info=True)
        return None


def reconcile_global_positions() -> list[tuple[str, ExitReason]]:
    """Close global legs when live price touches SL or target."""
    blob = _load()
    closed: list[tuple[str, ExitReason]] = []
    touched = False

    for row in blob.get("global") or []:
        if row.get("status") != "OPEN":
            continue
        sym = str(row.get("symbol") or "")
        px = _last_close_global_px(sym)
        if px is None:
            continue
        reason = _resolve_equity_row(row, px)
        if reason is None:
            continue
        row["status"] = "CLOSED"
        row["exit_reason"] = reason
        row["exit_price"] = round(px, 4)
        closed.append((sym, reason))
        touched = True

    if touched:
        _save(blob)
    return closed


def reconcile_all_positions() -> tuple[
    list[tuple[str, str, ExitReason]],
    list[tuple[str, ExitReason]],
    list[tuple[str, ExitReason]],
]:
    eq = reconcile_equity_positions()
    pr = reconcile_premium_positions()
    gl = reconcile_global_positions()
    return eq, pr, gl


def equity_position_open(symbol: str) -> bool:
    blob = _load()
    for row in blob["equity"]:
        if row.get("status") != "OPEN":
            continue
        if row.get("symbol") == symbol:
            return True
    return False


def global_position_open(symbol: str) -> bool:
    """True while BTC/ETH/XAU plan is active (between entry alert and SL/target)."""
    blob = _load()
    for row in blob.get("global") or []:
        if row.get("status") != "OPEN":
            continue
        if row.get("symbol") == symbol:
            return True
    return False


def global_signal_blocked(
    symbol: str,
    side: str,
    entry: float,
    stop_loss: float,
    target: float,
) -> str | None:
    """
    Block repeat alerts in the same price zone (open plan or prior signal range today).
    Returns a short reason string, or None if OK to send.
    """
    from config import GLOBAL_DEDUP_ENTRY_PCT

    blob = _load()
    tol = max(0.1, GLOBAL_DEDUP_ENTRY_PCT)

    for row in blob.get("global") or []:
        if row.get("symbol") != symbol:
            continue
        if row.get("side") != side:
            continue

        if row.get("status") == "OPEN":
            return "active plan awaiting SL/Target"

        prev_entry = float(row.get("entry") or 0)
        prev_sl = float(row.get("stop_loss") or 0)
        prev_tp = float(row.get("target") or 0)
        if prev_entry <= 0:
            continue

        if abs(entry - prev_entry) / prev_entry * 100.0 <= tol:
            return "duplicate entry (same zone as prior signal)"

        band = sorted([prev_entry, prev_sl, prev_tp])
        if band[0] <= entry <= band[1]:
            return "entry inside prior SL/entry band"
        if band[1] <= entry <= band[2]:
            return "entry inside prior entry/target range"

        trade_lo = min(prev_entry, prev_tp)
        trade_hi = max(prev_entry, prev_tp)
        if trade_lo <= entry <= trade_hi:
            return "entry still in prior trade range"

    return None


def register_global_open(
    *,
    symbol: str,
    strategy: str,
    side: str,
    entry: float,
    stop_loss: float,
    target: float,
) -> None:
    blob = _load()
    blob.setdefault("global", [])
    blob["global"].append(
        {
            "symbol": symbol,
            "strategy": strategy,
            "side": side,
            "entry": float(entry),
            "stop_loss": float(stop_loss),
            "target": float(target),
            "status": "OPEN",
        }
    )
    _save(blob)


def _option_slot_key(instrument: str, side_label: str) -> str:
    return f"{instrument}|{side_label}"


def premium_position_open(
    side_label: str,
    instrument: str = "NIFTY_OPTION",
    *,
    strategy: str = "",
) -> bool:
    blob = _load()
    for row in blob["premium"]:
        if row.get("status") != "OPEN":
            continue
        row_inst = str(row.get("instrument") or "NIFTY_OPTION")
        if row_inst != instrument or row.get("side_label") != side_label:
            continue
        if strategy and row.get("strategy") != strategy:
            continue
        return True
    return False


def peek_stock_exit_flag(symbol: str) -> ExitReason | None:
    """Outstanding exit awaiting first new Telegram alert for symbol (same IST day)."""
    blob = _load()
    raw = (blob.get("reentry_stock") or {}).get(symbol)
    if raw in ("TARGET_HIT", "STOP_LOSS"):
        return raw  # type: ignore[return-value]
    return None


def dismiss_stock_exit_flag(symbol: str) -> None:
    blob = _load()
    re_map = dict(blob.get("reentry_stock") or {})
    re_map.pop(symbol, None)
    blob["reentry_stock"] = re_map
    _save(blob)


def peek_option_exit_flag(side_label: str, instrument: str = "NIFTY_OPTION") -> ExitReason | None:
    blob = _load()
    raw = (blob.get("reentry_option") or {}).get(_option_slot_key(instrument, side_label))
    if raw in ("TARGET_HIT", "STOP_LOSS"):
        return raw  # type: ignore[return-value]
    return None


def dismiss_option_exit_flag(side_label: str, instrument: str = "NIFTY_OPTION") -> None:
    blob = _load()
    re_map = dict(blob.get("reentry_option") or {})
    re_map.pop(_option_slot_key(instrument, side_label), None)
    blob["reentry_option"] = re_map
    _save(blob)


def register_equity_open(signal: Signal, strategy_name: str) -> None:
    blob = _load()
    lv = signal.levels
    blob["equity"].append(
        {
            "symbol": signal.symbol,
            "strategy": strategy_name,
            "side": signal.side,
            "entry": float(lv.entry),
            "stop_loss": float(lv.stop_loss),
            "target": float(lv.primary_target),
            "status": "OPEN",
        }
    )
    _save(blob)


def register_premium_open(sig: Signal) -> None:
    if not sig.strike or int(sig.strike) <= 0:
        logger.warning("Skip premium lifecycle register — invalid strike.")
        return
    blob = _load()
    lv = sig.levels
    opt = (sig.option_type or "CE").upper()
    blob["premium"].append(
        {
            "symbol": sig.symbol,
            "instrument": sig.instrument or "NIFTY_OPTION",
            "side_label": sig.side,
            "strategy": sig.strategy,
            "strike": int(sig.strike or 0),
            "option_type": opt,
            "entry": float(lv.entry),
            "stop_loss": float(lv.stop_loss),
            "target": float(lv.primary_target),
            "status": "OPEN",
        }
    )
    _save(blob)

def equity_candidate_score(sig: Signal) -> float:
    lv = sig.levels
    rr = getattr(lv, "risk_reward_best", 0.0) or 0.0
    tp_pct = lv.target_profit_pct(sig.side)
    rk = getattr(lv, "risk_pct", 0.5)
    rk = max(float(rk), 0.05)
    # Prefer higher RR and reward % vs tight risk %
    return rr * 12.0 + tp_pct + 5.0 / rk


def equity_buy_sent_today() -> int:
    blob = _load()
    return int(blob.get("equity_buy_sent_today") or 0)


def increment_equity_buy_sent() -> None:
    blob = _load()
    blob["equity_buy_sent_today"] = int(blob.get("equity_buy_sent_today") or 0) + 1
    _save(blob)
