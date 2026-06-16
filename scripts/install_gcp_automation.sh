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

pkill -f "gcp_scheduler_daemon.py" 2>/dev/null || true
sleep 1

export TZ=Asia/Kolkata
nohup "$PY" scripts/gcp_scheduler_daemon.py >> "$LOG/scheduler.log" 2>&1 &
echo "Scheduler daemon PID: $!"
echo "Logs: tail -f $LOG/scheduler.log"
echo ""
echo "Schedule (IST):"
echo "  • Commands 24/7 (/upstox_token /live)"
echo "  • NSE Mon-Fri 9:10 AM"
echo "  • Global 7-8 AM & 4-10 PM"
echo ""
echo "Disable cron-job.org on your PC to avoid duplicate alerts."
