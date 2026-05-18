"""
Port of TradingView indicator "Intraday BUY/SELL & AUTO SL by chaitu50c" (Pine v6).

Bar-for-bar session state: single/double candle breaks, optional enhanced mode
(zone suppression, SL hit → opposite signal, slModeOnly after first SL).

Scanner uses the last completed bar in the session as the signal bar (matches
live bar-close alerts on TradingView when data includes the latest close).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Literal

import pandas as pd

IST_SESSION_START = time(9, 15)
IST_SESSION_END = time(15, 25)


def _in_chaitu_session(ts: pd.Timestamp) -> bool:
    """Match Pine session 0915-1525 on exchange (IST) clock."""
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    local = ts.tz_convert("Asia/Kolkata")
    t = local.time()
    return IST_SESSION_START <= t <= IST_SESSION_END


@dataclass
class ChaituParams:
    enhanced_mode: bool = True
    enable_buy: bool = True
    enable_sell: bool = True
    enable_double: bool = True
    unique_signal: bool = False


@dataclass
class ChaituFireResult:
    side: Literal["BUY", "SELL"]
    entry: float
    stop_level: float
    double_candle: bool
    reason: str


def _session_int(ts: pd.Timestamp) -> int:
    return 1 if _in_chaitu_session(ts) else 0


def replay_last_bar_signal(
    session: pd.DataFrame,
    params: ChaituParams | None = None,
) -> ChaituFireResult | None:
    """
    Replay the full session and return a signal only if one fires on the
    last row of ``session`` (same bar as Pine's most recent close).
    """
    params = params or ChaituParams()
    if len(session) < 3:
        return None

    idx = session.index
    o = session["Open"].astype(float)
    h = session["High"].astype(float)
    lo = session["Low"].astype(float)
    c = session["Close"].astype(float)

    lines: list[dict] = []

    sl_mode_only = False
    last_signal = 0

    def clear_all() -> None:
        nonlocal lines, sl_mode_only, last_signal
        lines = []
        sl_mode_only = False
        last_signal = 0

    last_bar = len(session) - 1
    fire_on_last: ChaituFireResult | None = None

    for i in range(2, len(session)):
        ts = idx[i]
        ts1 = idx[i - 1]
        ts2 = idx[i - 2]

        in_sess = _in_chaitu_session(ts)
        in_sess_int = 1 if in_sess else 0
        prev1_in_sess = _session_int(ts1) == 1
        prev2_in_sess = _session_int(ts2) == 1
        new_session = in_sess and not prev1_in_sess

        if new_session:
            clear_all()

        is_green = c.iloc[i] > o.iloc[i]
        is_red = c.iloc[i] < o.iloc[i]
        prev_green = prev1_in_sess and c.iloc[i - 1] > o.iloc[i - 1]
        prev_red = prev1_in_sess and c.iloc[i - 1] < o.iloc[i - 1]
        prev2_green = prev2_in_sess and c.iloc[i - 2] > o.iloc[i - 2]
        prev2_red = prev2_in_sess and c.iloc[i - 2] < o.iloc[i - 2]

        buy1 = (
            in_sess
            and params.enable_buy
            and is_green
            and prev_red
            and (c.iloc[i] > h.iloc[i - 1])
        )
        sell1 = (
            in_sess
            and params.enable_sell
            and is_red
            and prev_green
            and (c.iloc[i] < lo.iloc[i - 1])
        )
        buy2 = (
            params.enable_double
            and in_sess
            and params.enable_buy
            and is_green
            and prev_green
            and prev2_red
            and (c.iloc[i] > h.iloc[i - 2])
        )
        sell2 = (
            params.enable_double
            and in_sess
            and params.enable_sell
            and is_red
            and prev_red
            and prev2_green
            and (c.iloc[i] < lo.iloc[i - 2])
        )

        buy_cond_base = buy1 or buy2
        sell_cond_base = sell1 or sell2

        has_active_buy = False
        has_active_sell = False
        for ln in lines:
            if ln["active"]:
                if ln["kind"] == 1:
                    has_active_buy = True
                elif ln["kind"] == -1:
                    has_active_sell = True

        if params.unique_signal:
            buy_cond = buy_cond_base and (last_signal != 1) and (not new_session)
            sell_cond = sell_cond_base and (last_signal != -1) and (not new_session)
        else:
            buy_cond = buy_cond_base and (not has_active_buy) and (not new_session)
            sell_cond = sell_cond_base and (not has_active_sell) and (not new_session)

        broke_this_bar = False
        forced_opposite_done = False
        block_buy_this_bar = False
        block_sell_this_bar = False

        for j, ln in enumerate(lines):
            if not ln["active"]:
                continue
            k = ln["kind"]
            lvl = ln["level"]
            b_at = ln["born_bar"]
            brk = (k == 1 and c.iloc[i] < lvl and i > b_at) or (
                k == -1 and c.iloc[i] > lvl and i > b_at
            )
            if brk:
                ln["active"] = False
                broke_this_bar = True
                if params.enhanced_mode and (not forced_opposite_done):
                    if k == 1:
                        lvl_new = float(h.iloc[i])
                        lines.append(
                            {
                                "level": lvl_new,
                                "kind": -1,
                                "active": True,
                                "born_bar": i,
                                "entry": float(c.iloc[i]) if params.enhanced_mode else None,
                            }
                        )
                        last_signal = -1
                        block_sell_this_bar = True
                        sl_mode_only = True
                        forced_opposite_done = True
                        if i == last_bar:
                            fire_on_last = ChaituFireResult(
                                side="SELL",
                                entry=float(c.iloc[i]),
                                stop_level=lvl_new,
                                double_candle=False,
                                reason="AUTO opposite after BUY SL (enhanced)",
                            )
                    else:
                        lvl_new = float(lo.iloc[i])
                        lines.append(
                            {
                                "level": lvl_new,
                                "kind": 1,
                                "active": True,
                                "born_bar": i,
                                "entry": float(c.iloc[i]) if params.enhanced_mode else None,
                            }
                        )
                        last_signal = 1
                        block_buy_this_bar = True
                        sl_mode_only = True
                        forced_opposite_done = True
                        if i == last_bar:
                            fire_on_last = ChaituFireResult(
                                side="BUY",
                                entry=float(c.iloc[i]),
                                stop_level=lvl_new,
                                double_candle=False,
                                reason="AUTO opposite after SELL SL (enhanced)",
                            )

        active_buy_lvl = float("nan")
        active_sell_lvl = float("nan")
        active_buy_entry = float("nan")
        active_sell_entry = float("nan")

        for j, ln in enumerate(lines):
            if not ln["active"]:
                continue
            if ln["kind"] == 1:
                active_buy_lvl = ln["level"]
                ent = ln.get("entry")
                if params.enhanced_mode and ent is not None:
                    active_buy_entry = ent
            if ln["kind"] == -1:
                active_sell_lvl = ln["level"]
                ent = ln.get("entry")
                if params.enhanced_mode and ent is not None:
                    active_sell_entry = ent

        inside_buy_red_zone = (
            params.enhanced_mode
            and active_buy_lvl == active_buy_lvl
            and not pd.isna(active_buy_lvl)
            and not pd.isna(active_buy_entry)
            and min(active_buy_lvl, active_buy_entry)
            <= c.iloc[i]
            <= max(active_buy_lvl, active_buy_entry)
        )
        inside_sell_red_zone = (
            params.enhanced_mode
            and not pd.isna(active_sell_lvl)
            and not pd.isna(active_sell_entry)
            and min(active_sell_entry, active_sell_lvl)
            <= c.iloc[i]
            <= max(active_sell_entry, active_sell_lvl)
        )

        buy_fire = (
            (buy_cond and not inside_sell_red_zone and not sl_mode_only)
            if params.enhanced_mode
            else buy_cond
        )
        sell_fire = (
            (sell_cond and not inside_buy_red_zone and not sl_mode_only)
            if params.enhanced_mode
            else sell_cond
        )

        if buy_fire and (not block_buy_this_bar):
            lvl = (
                min(lo.iloc[i], lo.iloc[i - 1])
                if buy1
                else min(lo.iloc[i], lo.iloc[i - 1], lo.iloc[i - 2])
            )
            lines.append(
                {
                    "level": float(lvl),
                    "kind": 1,
                    "active": True,
                    "born_bar": i,
                    "entry": float(c.iloc[i]) if params.enhanced_mode else None,
                }
            )
            last_signal = 1
            if i == last_bar:
                fire_on_last = ChaituFireResult(
                    side="BUY",
                    entry=float(c.iloc[i]),
                    stop_level=float(lvl),
                    double_candle=bool(buy2),
                    reason="Chaitu50c BUY (single/double candle break)",
                )

        if sell_fire and (not block_sell_this_bar):
            lvl = (
                max(h.iloc[i], h.iloc[i - 1])
                if sell1
                else max(h.iloc[i], h.iloc[i - 1], h.iloc[i - 2])
            )
            lines.append(
                {
                    "level": float(lvl),
                    "kind": -1,
                    "active": True,
                    "born_bar": i,
                    "entry": float(c.iloc[i]) if params.enhanced_mode else None,
                }
            )
            last_signal = -1
            if i == last_bar:
                fire_on_last = ChaituFireResult(
                    side="SELL",
                    entry=float(c.iloc[i]),
                    stop_level=float(lvl),
                    double_candle=bool(sell2),
                    reason="Chaitu50c SELL (single/double candle break)",
                )

    return fire_on_last
