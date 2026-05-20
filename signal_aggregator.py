"""
Validate strategy signals and send one Telegram alert per strategy when it fires.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

from config import EMA_MIN_TARGET_PROFIT_PCT, MIN_STRATEGIES_TO_CONFIRM, SIGNALS_ONLY_TELEGRAM
from trade_filters import min_equity_target_profit_pct
from market_time import now_ist
from risk import TradeLevels, clamp_levels_to_playbook
from telegram_client import Signal

logger = logging.getLogger(__name__)

ENTRY_STRATEGIES = {
    "Setup 1: 1-Min Morning Breakout",
    "Setup 2: Core Price Action (5m/15m)",
    "Setup 3: Chaitu50c BUY/SELL",
    "Chaitu50c",
    "EMA 9/15 Crossover",
    "EMA 9/21 Crossover",
}


@dataclass
class ConfirmedSignal:
    symbol: str
    side: str
    levels: TradeLevels
    strategies: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    confidence: str = "MEDIUM"
    kind: str = "ENTRY"
    risk_mode: str = "playbook"
    suggested_qty: int = 0

    def to_telegram_signal(self) -> Signal:
        ts = now_ist().strftime("%d %b %Y, %H:%M IST")
        name = self.strategies[0] if len(self.strategies) == 1 else " + ".join(self.strategies)
        if SIGNALS_ONLY_TELEGRAM:
            return Signal(
                symbol=self.symbol,
                strategy=name,
                side=self.side,
                levels=self.levels,
                note="",
                kind=self.kind,  # type: ignore[arg-type]
                timeframe="",
                timestamp=ts,
                risk_mode=self.risk_mode,
                suggested_qty=self.suggested_qty,
                option_points_mode=False,
            )
        strat_label = " + ".join(self.strategies)
        note_parts = [f"Confirmed by {len(self.strategies)} strategy(s): {strat_label}."]
        if self.confidence == "HIGH":
            note_parts.insert(0, f"HIGH confidence — {len(self.strategies)} strategies agree.")
        note_parts.extend(self.notes[:3])
        return Signal(
            symbol=self.symbol,
            strategy=f"Combined Alert ({len(self.strategies)} strategies)",
            side=self.side,
            levels=self.levels,
            note=" ".join(note_parts),
            kind=self.kind,  # type: ignore[arg-type]
            timeframe="Multi-strategy",
            timestamp=ts,
            option_points_mode=False,
        )


def _merge_levels(signals: list[Signal], side: str) -> TradeLevels | None:
    """Conservative merge: best R:R among agreeing strategies."""
    if not signals:
        return None

    best = max(signals, key=lambda s: s.levels.risk_reward_best)
    entries = [s.levels.entry for s in signals]
    sls = [s.levels.stop_loss for s in signals]
    t1s = [s.levels.target_1 for s in signals]
    t2s = [s.levels.target_2 for s in signals]

    if side == "BUY":
        entry = statistics.median(entries)
        stop_loss = min(sls)
        target_1 = statistics.median(t1s)
        target_2 = max(t2s)
        best_target = max(s.levels.primary_target for s in signals)
    else:
        entry = statistics.median(entries)
        stop_loss = max(sls)
        target_1 = statistics.median(t1s)
        target_2 = min(t2s)
        best_target = min(s.levels.primary_target for s in signals)

    risk = abs(entry - stop_loss)
    if risk < 0.01:
        return None

    return TradeLevels(
        entry=round(entry, 2),
        stop_loss=round(stop_loss, 2),
        target_1=round(target_1, 2),
        target_2=round(target_2, 2),
        best_target=round(best_target, 2),
        rr_best=best.levels.rr_best,
        trailing_note=best.levels.trailing_note,
        risk=round(risk, 2),
        reward_1=round(abs(target_1 - entry), 2),
        reward_2=round(abs(target_2 - entry), 2),
    )


def confirm_single_signal(sig: Signal) -> ConfirmedSignal | None:
    """Validate one strategy hit; send as soon as it qualifies (no merge with others)."""
    if sig.kind == "EXIT":
        return ConfirmedSignal(
            symbol=sig.symbol,
            side=sig.side,
            levels=sig.levels,
            strategies=[sig.strategy],
            notes=[sig.note] if sig.note else [],
            confidence="EXIT",
            kind="EXIT",
        )

    if sig.kind != "ENTRY":
        return None

    side = sig.side
    ema_mode = getattr(sig, "risk_mode", "playbook") == "ema"
    if ema_mode:
        from risk import levels_ema_crossover

        levels = levels_ema_crossover(sig.levels.entry, sig.levels.stop_loss, side)
        min_profit = EMA_MIN_TARGET_PROFIT_PCT
    else:
        levels = clamp_levels_to_playbook(sig.levels, side)
        min_profit = min_equity_target_profit_pct()
    if levels is None:
        logger.info("Skip %s %s [%s] — risk clamp rejected.", sig.symbol, side, sig.strategy)
        return None

    profit_pct = levels.target_profit_pct(side)
    if profit_pct < min_profit:
        logger.info(
            "Skip %s %s [%s] — target profit %.2f%% below minimum %.2f%%.",
            sig.symbol,
            side,
            sig.strategy,
            profit_pct,
            min_profit,
        )
        return None

    return ConfirmedSignal(
        symbol=sig.symbol,
        side=side,
        levels=levels,
        strategies=[sig.strategy],
        notes=[sig.note] if sig.note else [],
        confidence="MEDIUM",
        kind="ENTRY",
        risk_mode=getattr(sig, "risk_mode", "playbook"),
        suggested_qty=getattr(sig, "suggested_qty", 0),
    )


def confirm_signals(raw: list[Signal]) -> ConfirmedSignal | None:
    """
    Merge strategy outputs for one symbol into a single confirmed trade.
    - EXIT signals pass through immediately (one per symbol).
    - ENTRY: same side only; need MIN_STRATEGIES_TO_CONFIRM agreeing.
    """
    if not raw:
        return None

    exits = [s for s in raw if s.kind == "EXIT"]
    if exits:
        s = exits[0]
        return ConfirmedSignal(
            symbol=s.symbol,
            side=s.side,
            levels=s.levels,
            strategies=[s.strategy],
            notes=[s.note] if s.note else [],
            confidence="EXIT",
            kind="EXIT",
        )

    entries = [s for s in raw if s.kind == "ENTRY" and s.strategy in ENTRY_STRATEGIES]
    if not entries:
        entries = [s for s in raw if s.kind == "ENTRY"]
    if not entries:
        return None

    buys = [s for s in entries if s.side == "BUY"]
    sells = [s for s in entries if s.side == "SELL"]

    if buys and sells:
        logger.info("Skip %s — conflicting BUY and SELL from strategies.", entries[0].symbol)
        return None

    group = buys if buys else sells
    side = "BUY" if buys else "SELL"

    if len(group) < MIN_STRATEGIES_TO_CONFIRM:
        logger.info(
            "Skip %s %s — only %s/%s strategies agree.",
            group[0].symbol,
            side,
            len(group),
            MIN_STRATEGIES_TO_CONFIRM,
        )
        return None

    levels = _merge_levels(group, side)
    if levels is None:
        return None

    ema_mode = all(getattr(s, "risk_mode", "playbook") == "ema" for s in group)
    if ema_mode:
        from risk import levels_ema_crossover

        levels = levels_ema_crossover(levels.entry, levels.stop_loss, side)
        min_profit = EMA_MIN_TARGET_PROFIT_PCT
    else:
        levels = clamp_levels_to_playbook(levels, side)
        min_profit = min_equity_target_profit_pct()
    if levels is None:
        logger.info("Skip %s %s — risk clamp rejected merged levels.", group[0].symbol, side)
        return None

    profit_pct = levels.target_profit_pct(side)
    if profit_pct < min_profit:
        logger.info(
            "Skip %s %s — target profit %.2f%% below minimum %.2f%%.",
            group[0].symbol,
            side,
            profit_pct,
            min_profit,
        )
        return None

    confidence = "HIGH" if len(group) >= 2 else "MEDIUM"
    return ConfirmedSignal(
        symbol=group[0].symbol,
        side=side,
        levels=levels,
        strategies=[s.strategy for s in group],
        notes=[s.note for s in group if s.note][:4],
        confidence=confidence,
        kind="ENTRY",
        risk_mode=getattr(group[0], "risk_mode", "playbook"),
        suggested_qty=max((getattr(s, "suggested_qty", 0) for s in group), default=0),
    )


def collect_raw_signals(symbol: str, scanners: list, names: list[str]) -> list[Signal]:
    raw: list[Signal] = []
    for fn, name in zip(scanners, names):
        try:
            sig = fn(symbol)
            if sig:
                raw.append(sig)
        except Exception:
            logger.exception("Strategy %s failed for %s", name, symbol)
    return raw
