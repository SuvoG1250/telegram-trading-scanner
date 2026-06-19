#!/usr/bin/env bash
# Install GCP automation without crontab (for VMs without sudo/cron)
set -euo pipefail

APP="$HOME/telegram-trading-scanner"
PY="$APP/venv/bin/python"
LOG="$HOME/tradingbot-logs"
mkdir -p "$LOG"
cd "$APP"

if [[ ! -f .env ]]; then
  echo "Missing $APP/.env"
  exit 1
fi

# Ensure only ONE Telegram poller and ONE scheduler (duplicate getUpdates = Conflict errors)
echo "Stopping duplicate Telegram pollers and schedulers..."
pkill -f "telegram_command_listener.py" 2>/dev/null || true
pkill -f "gcp_scheduler_daemon.py" 2>/dev/null || true
sleep 2

LISTENERS=$(pgrep -fc "telegram_command_listener.py" 2>/dev/null || echo 0)
SCHEDULERS=$(pgrep -fc "gcp_scheduler_daemon.py" 2>/dev/null || echo 0)
if [[ "$LISTENERS" != "0" ]] || [[ "$SCHEDULERS" != "0" ]]; then
  echo "Force-killing remaining processes..."
  pkill -9 -f "telegram_command_listener.py" 2>/dev/null || true
  pkill -9 -f "gcp_scheduler_daemon.py" 2>/dev/null || true
  sleep 1
fi

# Session runner must NOT poll when daemon listener is active
if ! grep -q "TELEGRAM_POLL_IN_SESSION" .env 2>/dev/null; then
  echo "TELEGRAM_POLL_IN_SESSION=false" >> .env
  echo "Added TELEGRAM_POLL_IN_SESSION=false to .env"
fi

export TZ=Asia/Kolkata
nohup "$PY" scripts/gcp_scheduler_daemon.py >> "$LOG/scheduler.log" 2>&1 &
echo "Scheduler daemon PID: $!"
sleep 3

LISTENERS=$(pgrep -fc "telegram_command_listener.py" 2>/dev/null || echo 0)
echo "Telegram listeners running: $LISTENERS (should be 1)"
if [[ "$LISTENERS" != "1" ]]; then
  echo "WARNING: expected exactly 1 listener. Check: pgrep -af telegram_command_listener"
fi

echo "Logs: tail -f $LOG/scheduler.log  and  tail -f $LOG/commands.log"
echo ""
echo "Schedule (IST):"
echo "  • Commands 24/7 (/upstox_token /strategy)"
echo "  • NSE Mon-Fri 9:10 AM"
echo "  • Global 7-8 AM & 4-10 PM"
echo ""
echo "Do NOT also run scripts/start_gcp_now.sh (creates duplicate listener)."
echo "Disable cron-job.org + GitHub telegram-commands workflow on your PC."
