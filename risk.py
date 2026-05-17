"""Stop-loss and target calculations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradeLevels:
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    trailing_note: str
    risk: float
    reward_1: float
    reward_2: float


def _round_price(value: float) -> float:
    return round(value, 2)


def levels_for_long(entry: float, trigger_low: float) -> TradeLevels:
    risk = max(entry - trigger_low, 0.01)
    t1 = entry + risk * 1.5
    t2 = entry + risk * 2.0
    return TradeLevels(
        entry=_round_price(entry),
        stop_loss=_round_price(trigger_low),
        target_1=_round_price(t1),
        target_2=_round_price(t2),
        trailing_note="Trail SL below each 5m higher low after T2 is hit.",
        risk=_round_price(risk),
        reward_1=_round_price(t1 - entry),
        reward_2=_round_price(t2 - entry),
    )


def levels_for_short(entry: float, trigger_high: float) -> TradeLevels:
    risk = max(trigger_high - entry, 0.01)
    t1 = entry - risk * 1.5
    t2 = entry - risk * 2.0
    return TradeLevels(
        entry=_round_price(entry),
        stop_loss=_round_price(trigger_high),
        target_1=_round_price(t1),
        target_2=_round_price(t2),
        trailing_note="Trail SL above each 5m lower high after T2 is hit.",
        risk=_round_price(risk),
        reward_1=_round_price(entry - t1),
        reward_2=_round_price(entry - t2),
    )
