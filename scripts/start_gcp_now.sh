#!/usr/bin/env bash
# DEPRECATED: use scripts/install_gcp_automation.sh instead (avoids duplicate Telegram pollers)
set -euo pipefail

APP="$HOME/telegram-trading-scanner"
PY="$APP/venv/bin/python"
LOG="$HOME/tradingbot-logs"
mkdir -p "$LOG"
cd "$APP"

if pgrep -f "gcp_scheduler_daemon.py" >/dev/null 2>&1; then
  echo "gcp_scheduler_daemon is already running — it manages the Telegram listener."
  echo "Use: bash scripts/install_gcp_automation.sh"
  echo "Only start NSE session manually if needed:"
  echo "  nohup $PY upstox_live_runner.py --max-minutes 390 >> $LOG/nse-session.log 2>&1 &"
  exit 1
fi

export TZ=Asia/Kolkata
"$PY" scripts/telegram_delete_webhook.py || true

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
