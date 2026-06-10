# Telegram Alerts — Quick Reference

One-page cheat sheet for what this bot sends (and what it does not).

---

## Destinations

Alerts go to every chat in `TELEGRAM_GROUP_CHAT_ID`, `TELEGRAM_CHAT_ID`, and `TELEGRAM_CHAT_IDS`.

### Telegram commands (Upstox live trading)

Cloud automation listens every day (9:10 AM IST). Send commands in the bot chat:

| Command | Action |
|---|---|
| `/live` | **Real** Upstox option orders (Nifty/Sensex CE/PE) |
| `/paper` | Test mode — log only |
| `/stop` | Disable Upstox orders |
| `/status` | Current mode + connection |
| `/lots 1` | Option lots (1–10) |
| `/help` | Command list |

---

## What IS sent

### 1. NSE stocks (intraday)

| | |
|---|---|
| **When** | Mon–Fri, **9:15 AM – 3:00 PM IST** |
| **Strategies** | Setup 1 (1m breakout), Setup 2 (5m/15m PA), EMA 9/15, EMA 9/21, EMA20+ST |
| **Caps** | 1 BUY/scan · max 10 BUY/day · 1 SHORT/scan |

```
🟢 STOCK BUY: RELIANCE  ·  F&O · MIS
📊 Strategy: EMA 9/15 Crossover
⏱ Chart: 5 Min
Entry Rs 2,450.00  ·  SL Rs 2,435.00  ·  Target Rs 2,480.00
+1.22% · 1:2 R:R · SL 0.61% ·  28 May 2026, 10:15 IST
```

Optional: `🤖 AI: …` · re-entry flag after prior SL/target.

---

### 2. Pre-market summary (news + sentiment)

| | |
|---|---|
| **When** | Mon–Fri, **9:10 – 9:26 AM IST** (once per day) |
| **Source** | Google News + yfinance headlines |
| **Includes** | **Positive** and **negative** headline buckets, Nifty gap, global mood |

```
📰 Pre-Market Summary — 9 Jun 2026, 9:12 IST

🟢 Today's bias: bullish | News: bullish | Nifty +0.45% | Global: positive

✅ Positive
• …

❌ Negative / risks
• …
```

---

### 3. Nifty + Sensex options (intraday)

Two independent option scanners (same premium plan: SL −₹15 · book +₹30 · trail +₹100).

| Scanner | Trigger |
|---|---|
| **ST+TSL** | SuperTrend flip (exit490) on 5m |
| **EMA 9/21 + MACD** | EMA cross on **Heikin Ashi 3m** + MACD hist green/red (34/144/9) |

| | |
|---|---|
| **When** | Mon–Fri, **9:15 AM – 3:00 PM IST** |
| **EMA+MACD rules** | MACD **green** → only **BUY CALL** (EMA 9 crosses above 21) · MACD **red** → only **BUY PUT** (EMA 9 crosses below 21) |
| **Rule** | One active CALL or PUT **per index** until SL/target (shared across both scanners) |

```
🟢 BUY CALL — NIFTY
Strategy: EMA 9/21 + MACD Options
Strike: 23500 CE  ·  Expiry: …
…
EMA 9 crossed above EMA 21 · MACD hist green (34/144/9) · Heikin Ashi 3m
```

```
🟢 BUY CALL — NIFTY
Strategy: Nifty ST+TSL Options
Strike: 24500 CE  ·  Expiry: 29 May 2026
Premium entry: ₹120.50 (live Upstox LTP)
SL premium: ₹105.50  ·  T1: ₹135.50  ·  Target: ₹150.50
SL −₹15 · Book +₹30 · Trail up to +₹100  ·  1:2 R:R  ·  <time>
```

Sensex uses **100-point strikes**, Thursday weekly expiry, same premium plan.

---

### 4. Stock Gap-Up BTST — BUY only (all NSE under Rs 1000)

| | |
|---|---|
| **When** | Mon–Fri, **3:10 – 3:20 PM IST** |
| **Universe** | **All NSE stocks** with price **Rs 50 – Rs 1000** |
| **Side** | **BUY only** (overnight equity, CNC) |
| **Focus** | **Gap-up potential** + fundamental + news |
| **Confirm** | ≥75% checks |
| **Max** | 3 stocks per day |

**Flow:** batch screen full NSE → gap-up filter → fundamental + news → top picks only.

---

### 5. Nifty BTST (once per day)

| | |
|---|---|
| **When** | Mon–Fri, **3:20 – 3:30 PM IST** |
| **Confirm** | >80% of 6 checks (≥5/6 pass) |

**Confirmed** → full BUY CALL/PUT BTST + research checklist + headlines + optional AI.

**Not confirmed** → `⚠️ BTST RISKY TODAY — DO NOT TAKE BTST` + checklist + score.

---

### 6. Global — BTC / ETH / Gold

| | |
|---|---|
| **When** | **7:00 AM – 11:00 PM IST**, all days — **not during NSE hours (9:15–15:30 Mon–Fri)** |
| **Symbols** | BTCUSD, ETHUSD, XAUUSD |
| **Timeframe** | **1 Hour** (last closed candle only) |
| **SL / Target** | Swing structure (not fixed ATR/RR) |
| **Min R:R** | ≥ 1:3 from structure (max ~1:8) |
| **Dedup** | No repeat in same entry/target range until SL/target hit |

```
🟢 BTCUSD BUY — Bitcoin
Strategy: Global 1H Structure + Trend
Timeframe: 1 Hour (confirmed closed candle)
Entry: 67500.00
Stop Loss: 66200.00 (structure)
Target: 70100.00 (structure)
Risk:Reward: 1:3.45
Market Analysis: 1H bullish structure… SL at swing low; target at resistance
Outside NSE hours · 07:00–23:00 IST · <time>
```

---

### 7. NSE EOD summary (once per day)

| | |
|---|---|
| **When** | Mon–Fri, **once after 3:32 PM IST** (after market close + BTST) |
| **Includes** | Stocks + Nifty/Sensex options + BTST (journal only) |
| **Excludes** | Crypto/Gold |

```
📊 NSE Indian Market — EOD P/L Summary — <date>
Net P/L · wins/losses · per-trade rows · optional 🤖 AI day note
```

---

## What is NOT sent

| Message type | Default |
|--------------|---------|
| GLOBAL WINDOW RUN banner | Off |
| Health check / API status ping | Off |
| Session START / STOP | Off |
| Boot “bot running” ping | Off |
| Pre-market watchlist | Off |
| Pre-market news summary | **On** (`SEND_PREMARKET_MARKET_SUMMARY`) |
| Per-scan summary | Off |
| SL/Target cleared pings | Off |

---

## No duplicate signals until exit

| Market | Blocked while… |
|--------|----------------|
| Stocks | Same symbol plan is **OPEN** (between entry and SL/target) |
| Nifty options | Same CALL/PUT plan is **OPEN** |
| BTC / ETH / Gold | Same symbol **OPEN** plan, or entry in **prior signal range** (same day) |
| BTST | **1 message/day** (confirmed or risky) |

After SL or target is hit, a **new** alert is allowed (stocks/options may show a re-entry note).

---

## Daily timeline (IST)

```
07:00–09:15 ─────── Global (BTC/ETH/XAU)
09:10–09:26 ─────── Pre-market news summary (once)
09:15–15:30 ─────── NSE only — no global alerts
15:30–23:00 ─────── Global + (EOD summary once after 15:30)
Weekends ────────── Global only (full 7:00–23:00 window)
```

---

## Config flags (`.env` / GitHub Actions)

| Variable | Default | Effect |
|----------|---------|--------|
| `SEND_DAILY_SUMMARY` | `true` | EOD P/L after 3:30 PM |
| `SEND_SESSION_ALERTS` | `false` | Start/stop pings |
| `SEND_HEALTH_CHECK` | `false` | Morning health ping |
| `SEND_BOOT_ALERT` | `false` | Delayed boot ping |
| `SEND_PREMARKET_MARKET_SUMMARY` | `true` | News + sentiment at 9:10 AM |
| `SEND_PREMARKET_REPORT` | `false` | Full watchlist Telegram |
| `GLOBAL_ASSETS_ENABLED` | `true` | BTC/ETH/XAU |
| `NIFTY_BTST_MIN_CONFIRM_PCT` | `80` | BTST confirm threshold |
| `SLTP_CLOSE_ALERT_TELEGRAM` | `false` | SL/target hit pings |

---

*NSE: Mon–Fri 9:10 IST (390 min scan) · Global: 7–8 & 16–22 IST · Playbook ~45 stocks · yfinance throttled.*
