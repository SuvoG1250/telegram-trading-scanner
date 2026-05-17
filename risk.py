"""Stop-loss, targets, and risk-reward (professional trade plan)."""

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
    best_target: float | None = None
    rr_best: float = 1.5

    @property
    def primary_target(self) -> float:
        return self.best_target if self.best_target is not None else self.target_2

    @property
    def risk_reward_best(self) -> float:
        if self.risk <= 0:
            return 0.0
        if self.entry >= self.stop_loss:
            return round((self.primary_target - self.entry) / self.risk, 2)
        return round((self.entry - self.primary_target) / self.risk, 2)

    @property
    def risk_pct(self) -> float:
        if self.entry <= 0:
            return 0.0
        return round((self.risk / self.entry) * 100, 2)


def _round_price(value: float) -> float:
    return round(value, 2)


def levels_for_long(
    entry: float,
    trigger_low: float,
    rr1: float = 1.5,
    rr2: float = 2.0,
    best_rr: float | None = None,
) -> TradeLevels:
    risk = max(entry - trigger_low, 0.01)
    t1 = entry + risk * rr1
    t2 = entry + risk * rr2
    br = best_rr if best_rr is not None else rr2
    best = entry + risk * br
    return TradeLevels(
        entry=_round_price(entry),
        stop_loss=_round_price(trigger_low),
        target_1=_round_price(t1),
        target_2=_round_price(t2),
        best_target=_round_price(best),
        rr_best=br,
        trailing_note="Trail SL below last 5m swing low after T1 is hit.",
        risk=_round_price(risk),
        reward_1=_round_price(t1 - entry),
        reward_2=_round_price(t2 - entry),
    )


def levels_for_short(
    entry: float,
    trigger_high: float,
    rr1: float = 1.5,
    rr2: float = 2.0,
    best_rr: float | None = None,
) -> TradeLevels:
    risk = max(trigger_high - entry, 0.01)
    t1 = entry - risk * rr1
    t2 = entry - risk * rr2
    br = best_rr if best_rr is not None else rr2
    best = entry - risk * br
    return TradeLevels(
        entry=_round_price(entry),
        stop_loss=_round_price(trigger_high),
        target_1=_round_price(t1),
        target_2=_round_price(t2),
        best_target=_round_price(best),
        rr_best=br,
        trailing_note="Trail SL above last 5m swing high after T1 is hit.",
        risk=_round_price(risk),
        reward_1=_round_price(entry - t1),
        reward_2=_round_price(entry - t2),
    )
