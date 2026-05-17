"""Build validated professional trade signals for all strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from market_time import now_ist
from risk import TradeLevels, levels_for_long, levels_for_short
from telegram_client import Signal

SignalKind = Literal["ENTRY", "EXIT"]


@dataclass
class TradePlan:
    symbol: str
    strategy: str
    side: Literal["BUY", "SELL"]
    levels: TradeLevels
    note: str = ""
    kind: SignalKind = "ENTRY"
    timeframe: str = "Intraday"

    def to_signal(self) -> Signal:
        return Signal(
            symbol=self.symbol,
            strategy=self.strategy,
            side=self.side,
            levels=self.levels,
            note=self.note,
            kind=self.kind,
            timeframe=self.timeframe,
            timestamp=now_ist().strftime("%d %b %Y, %H:%M IST"),
        )


def validate_plan(plan: TradePlan) -> bool:
    lv = plan.levels
    if plan.kind == "EXIT":
        return True
    if lv.entry <= 0 or lv.stop_loss <= 0:
        return False
    if plan.side == "BUY":
        if lv.entry <= lv.stop_loss:
            return False
        if lv.primary_target <= lv.entry:
            return False
    else:
        if lv.entry >= lv.stop_loss:
            return False
        if lv.primary_target >= lv.entry:
            return False
    if lv.risk_pct > 5.0:
        return False
    return True


def entry_long(
    symbol: str,
    strategy: str,
    entry: float,
    stop_loss: float,
    *,
    rr1: float = 1.5,
    rr2: float = 2.0,
    best_rr: float | None = None,
    note: str = "",
    timeframe: str = "Intraday",
    trailing_note: str | None = None,
) -> Signal | None:
    levels = levels_for_long(entry, stop_loss, rr1=rr1, rr2=rr2, best_rr=best_rr)
    if trailing_note:
        levels.trailing_note = trailing_note
    plan = TradePlan(symbol, strategy, "BUY", levels, note, "ENTRY", timeframe)
    if not validate_plan(plan):
        return None
    return plan.to_signal()


def entry_short(
    symbol: str,
    strategy: str,
    entry: float,
    stop_loss: float,
    *,
    rr1: float = 1.5,
    rr2: float = 2.0,
    best_rr: float | None = None,
    note: str = "",
    timeframe: str = "Intraday",
    trailing_note: str | None = None,
) -> Signal | None:
    levels = levels_for_short(entry, stop_loss, rr1=rr1, rr2=rr2, best_rr=best_rr)
    if trailing_note:
        levels.trailing_note = trailing_note
    plan = TradePlan(symbol, strategy, "SELL", levels, note, "ENTRY", timeframe)
    if not validate_plan(plan):
        return None
    return plan.to_signal()


def exit_signal(
    symbol: str,
    strategy: str,
    exit_price: float,
    note: str,
    side: Literal["BUY", "SELL"] = "SELL",
) -> Signal:
    levels = TradeLevels(
        entry=_round(exit_price),
        stop_loss=_round(exit_price),
        target_1=_round(exit_price),
        target_2=_round(exit_price),
        best_target=_round(exit_price),
        rr_best=0,
        trailing_note="Close position now.",
        risk=0.0,
        reward_1=0.0,
        reward_2=0.0,
    )
    return TradePlan(
        symbol, strategy, side, levels, note, "EXIT", "Intraday"
    ).to_signal()


def _round(v: float) -> float:
    return round(v, 2)
