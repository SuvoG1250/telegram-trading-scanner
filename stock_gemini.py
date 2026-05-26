"""Gemini AI for NSE stock selection, scan ranking, and alert notes."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from config import (
    DATA_DIR,
    GEMINI_STOCK_ALERTS,
    GEMINI_STOCK_ENABLED,
    GEMINI_STOCK_RANK,
    GEMINI_STOCK_RANK_MAX,
    GEMINI_STOCK_SELECTION,
)
from gemini_client import gemini_generate, gemini_json, llm_available
from market_sentiment import assess_market_sentiment
from market_time import now_ist
from sector_map import sector_for

logger = logging.getLogger(__name__)

_CACHE_FILE = DATA_DIR / "gemini_stock_cache.json"
_FOCUS_BOOST = 2.5


def gemini_stock_enabled() -> bool:
    return llm_available() and GEMINI_STOCK_ENABLED


def _load_cache() -> dict[str, Any]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(blob: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(blob, indent=2), encoding="utf-8")


def _today() -> str:
    return now_ist().strftime("%Y-%m-%d")


def get_daily_focus_symbols() -> set[str]:
    if not gemini_stock_enabled() or not GEMINI_STOCK_SELECTION:
        return set()
    blob = _load_cache()
    if blob.get("date") != _today():
        return set()
    return set(blob.get("focus_symbols") or [])


def apply_focus_score_boost(symbol: str, score: float) -> float:
    if symbol in get_daily_focus_symbols():
        return score + _FOCUS_BOOST
    return score


def run_premarket_stock_selection(rows: list[dict]) -> str:
    """
    Once per IST day: Gemini picks top intraday focus names from playbook rows.
    Returns HTML block for pre-market Telegram (may be empty).
    """
    if not gemini_stock_enabled() or not GEMINI_STOCK_SELECTION or not rows:
        return ""

    blob = _load_cache()
    if blob.get("date") == _today() and blob.get("premarket_block"):
        return str(blob["premarket_block"])

    top = rows[:40]
    lines = []
    for r in top[:25]:
        sym = r.get("symbol", "")
        lines.append(
            f"{sym}|{r.get('sector', sector_for(sym))}|{r.get('pct_change', 0):+.2f}%|"
            f"{','.join(r.get('sources') or [])}"
        )

    sentiment = assess_market_sentiment()
    prompt = (
        "You are an Indian NSE intraday trader. From the watchlist lines (symbol|sector|"
        f"prior_day_pct|tags), pick the best 8 symbols for TODAY's intraday long/short "
        f"(F&O, MIS). Market: {sentiment.get('summary', '')} bias={sentiment.get('trade_bias')}.\n"
        "Lines:\n" + "\n".join(lines) + "\n"
        'Return JSON: {"focus":["SYM",...],"avoid":["SYM",...],"summary":"2-3 sentences for Telegram"}'
    )
    data = gemini_json(prompt, max_tokens=450)
    focus: list[str] = []
    summary = ""
    if isinstance(data, dict):
        focus = [str(s).upper().replace(".NS", "") for s in data.get("focus") or []][:10]
        summary = str(data.get("summary") or "").strip()[:500]

    if not focus and not summary:
        summary = gemini_generate(
            "In 2 sentences, name 3 NSE sectors to watch for intraday today (India).",
            max_tokens=120,
        )

    block = ""
    if focus or summary:
        focus_line = ", ".join(focus[:8]) if focus else "—"
        block = (
            "🤖 <b>AI stock focus (Gemini)</b>\n"
            f"<b>Priority:</b> {focus_line}\n"
            f"<i>{summary}</i>"
        )

    blob["date"] = _today()
    blob["focus_symbols"] = focus
    blob["premarket_block"] = block
    _save_cache(blob)
    return block


def rank_scan_candidates(
    ranked: list[tuple[Any, float, str]],
) -> list[tuple[Any, float, str]]:
    """Reorder top equity setups using Gemini (cached per scan fingerprint)."""
    if not gemini_stock_enabled() or not GEMINI_STOCK_RANK or len(ranked) < 2:
        return ranked

    cap = min(GEMINI_STOCK_RANK_MAX, len(ranked))
    slice_ = ranked[:cap]
    fingerprint = hashlib.md5(
        "|".join(f"{t[0].symbol}:{t[2]}:{t[1]:.1f}" for t in slice_).encode()
    ).hexdigest()[:12]

    blob = _load_cache()
    rank_cache = blob.get("rank_cache") or {}
    if rank_cache.get("fp") == fingerprint and rank_cache.get("order"):
        order = rank_cache["order"]
        by_sym = {t[0].symbol: t for t in ranked}
        out = [by_sym[s] for s in order if s in by_sym]
        out.extend(t for t in ranked if t[0].symbol not in order)
        return out

    items = []
    for confirmed, score, strat in slice_:
        sig = confirmed.to_telegram_signal()
        lv = sig.levels
        items.append(
            {
                "symbol": confirmed.symbol,
                "side": confirmed.side,
                "strategy": strat,
                "score": round(score, 2),
                "entry": lv.entry,
                "sl": lv.stop_loss,
                "target": lv.primary_target,
                "rr": getattr(lv, "risk_reward_best", 0),
                "sector": sector_for(confirmed.symbol),
            }
        )

    sentiment = assess_market_sentiment()
    prompt = (
        "Rank these NSE intraday trade candidates best-first for Telegram alert priority. "
        f"Market bias: {sentiment.get('trade_bias')}. Prefer strong R:R and clear trend.\n"
        f"Candidates JSON: {json.dumps(items)}\n"
        'Return JSON: {"priority":["SYM",...],"skip":[],"reason":"one line"}'
    )
    data = gemini_json(prompt, max_tokens=350)
    if not isinstance(data, dict):
        return ranked

    priority = [str(s).upper() for s in data.get("priority") or []]
    skip = {str(s).upper() for s in data.get("skip") or []}
    if not priority:
        return ranked

    by_sym = {t[0].symbol: t for t in ranked}
    out: list[tuple[Any, float, str]] = []
    for sym in priority:
        if sym in skip or sym not in by_sym:
            continue
        out.append(by_sym[sym])
    for t in ranked:
        if t[0].symbol not in {x[0].symbol for x in out} and t[0].symbol not in skip:
            out.append(t)

    blob.setdefault("date", _today())
    blob["rank_cache"] = {"fp": fingerprint, "order": [t[0].symbol for t in out[:cap]]}
    _save_cache(blob)
    logger.info("Gemini reordered %d scan candidates.", len(out))
    return out


def build_alert_ai_note(
    *,
    symbol: str,
    side: str,
    strategy: str,
    entry: float,
    stop_loss: float,
    target: float,
    timeframe: str = "",
) -> str:
    """Short AI rationale appended to stock Telegram alerts."""
    if not gemini_stock_enabled() or not GEMINI_STOCK_ALERTS:
        return ""

    key = f"{_today()}:{symbol}:{side}:{strategy}"
    blob = _load_cache()
    notes = blob.get("alert_notes") or {}
    if key in notes:
        return str(notes[key])

    prompt = (
        f"NSE intraday {side} on {symbol} ({sector_for(symbol)}). "
        f"Strategy: {strategy}. Entry {entry:.2f} SL {stop_loss:.2f} Target {target:.2f}. "
        f"Chart: {timeframe or 'intraday'}. "
        "In exactly 2 short bullet points (max 25 words each), say why this setup is valid "
        "or what to watch. Plain text, no HTML."
    )
    text = gemini_generate(prompt, max_tokens=150, temperature=0.25)
    if not text:
        return ""

    note = text.strip()[:320]
    notes[key] = note
    blob["alert_notes"] = notes
    blob.setdefault("date", _today())
    _save_cache(blob)
    return note
