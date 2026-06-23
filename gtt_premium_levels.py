"""Index-specific GTT stop-loss and target points on option premium."""

from __future__ import annotations

from config import (
    NIFTY_OPTION_PREMIUM_SL_POINTS,
    NIFTY_OPTION_PREMIUM_TARGET_POINTS,
    SENSEX_OPTION_PREMIUM_SL_POINTS,
    SENSEX_OPTION_PREMIUM_TARGET_POINTS,
)
from risk import TradeLevels

ExecutionIndex = str  # "nifty" | "sensex"


def instrument_index(instrument: str) -> ExecutionIndex:
    if (instrument or "").upper() == "SENSEX_OPTION":
        return "sensex"
    return "nifty"


def gtt_sl_target_points(instrument: str) -> tuple[float, float]:
    """Return (sl_points, target_points) for the index."""
    if instrument_index(instrument) == "sensex":
        return SENSEX_OPTION_PREMIUM_SL_POINTS, SENSEX_OPTION_PREMIUM_TARGET_POINTS
    return NIFTY_OPTION_PREMIUM_SL_POINTS, NIFTY_OPTION_PREMIUM_TARGET_POINTS


def gtt_prices(entry_premium: float, instrument: str) -> tuple[float, float, float]:
    """Return (entry, stop_loss, target) premium prices for GTT legs."""
    sl_pts, tgt_pts = gtt_sl_target_points(instrument)
    entry = round(float(entry_premium), 2)
    sl = round(max(entry - sl_pts, 0.5), 2)
    target = round(entry + tgt_pts, 2)
    return entry, sl, target


def trade_levels_from_entry(entry_premium: float, instrument: str) -> TradeLevels:
    """Alert + lifecycle levels aligned with GTT SL/target (no 100-pt trail)."""
    sl_pts, tgt_pts = gtt_sl_target_points(instrument)
    entry, sl, target = gtt_prices(entry_premium, instrument)
    risk = max(sl_pts, 1.0)
    idx = instrument_index(instrument).upper()
    return TradeLevels(
        entry=entry,
        stop_loss=sl,
        target_1=target,
        target_2=target,
        best_target=target,
        rr_best=round(tgt_pts / risk, 2) if risk > 0 else 0.0,
        trailing_note=(
            f"GTT: SL −₹{sl_pts:.0f} · Target +₹{tgt_pts:.0f} on premium ({idx}). "
            "Exit all by 3:25 PM IST."
        ),
        risk=round(risk, 2),
        reward_1=round(tgt_pts, 2),
        reward_2=round(tgt_pts, 2),
    )


def gtt_points_summary(instrument: str) -> str:
    sl_pts, tgt_pts = gtt_sl_target_points(instrument)
    idx = instrument_index(instrument).upper()
    return f"{idx} GTT: SL −₹{sl_pts:.0f} · Target +₹{tgt_pts:.0f}"
