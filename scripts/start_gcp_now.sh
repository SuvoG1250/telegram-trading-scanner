#!/usr/bin/env bash
# Start bot now on GCP (no sudo) — commands + optional NSE session
set -euo pipefail

APP="$HOME/telegram-trading-scanner"
PY="$APP/venv/bin/python"
LOG="$HOME/tradingbot-logs"
mkdir -p "$LOG"
cd "$APP"

export TZ=Asia/Kolkata
"$PY" scripts/telegram_delete_webhook.py || true

# Stop old listeners if re-running
pkill -f "telegram_command_listener.py" 2>/dev/null || true
pkill -f "upstox_live_runner.py" 2>/dev/null || true
sleep 1

nohup "$PY" scripts/telegram_command_listener.py --seconds 86400 --interval 1.5 \
  >> "$LOG/commands.log" 2>&1 &
echo "Commands listener PID: $!"

if [[ "${1:-}" == "--with-session" ]]; then
  nohup "$PY" upstox_live_runner.py --max-minutes 390 \
    >> "$LOG/nse-session.log" 2>&1 &
  echo "NSE session PID: $!"
fi

echo "Logs: tail -f $LOG/commands.log"
echo "Telegram: send /help"
