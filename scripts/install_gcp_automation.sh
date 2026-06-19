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

echo "=== Stopping ALL Telegram pollers and schedulers ==="
pkill -f "telegram_command_listener.py" 2>/dev/null || true
pkill -f "gcp_scheduler_daemon.py" 2>/dev/null || true
sleep 2
pkill -9 -f "telegram_command_listener.py" 2>/dev/null || true
pkill -9 -f "gcp_scheduler_daemon.py" 2>/dev/null || true
sleep 1
rm -f "$APP/data/telegram_poll.lock" 2>/dev/null || true

# Session runner must NOT poll when scheduler handles Telegram
if ! grep -q "^TELEGRAM_POLL_IN_SESSION=" .env 2>/dev/null; then
  echo "TELEGRAM_POLL_IN_SESSION=false" >> .env
  echo "Added TELEGRAM_POLL_IN_SESSION=false"
fi
if grep -q "^TELEGRAM_COMMANDS_ENABLED=" .env 2>/dev/null; then
  sed -i 's/^TELEGRAM_COMMANDS_ENABLED=.*/TELEGRAM_COMMANDS_ENABLED=true/' .env 2>/dev/null || true
else
  echo "TELEGRAM_COMMANDS_ENABLED=true" >> .env
fi

echo ""
echo "=== Pre-flight Telegram config ==="
"$PY" -c "
from config import telegram_commands_status, telegram_chat_ids
ok, msg = telegram_commands_status()
print('Ready:', ok, '-', msg)
print('Chat IDs:', telegram_chat_ids())
if not ok:
    raise SystemExit(1)
" || {
  echo ""
  echo "ERROR: Fix .env — TELEGRAM_TOKEN must be set."
  echo "Upload your .env from PC or edit: nano is not needed, use:"
  echo "  cat > .env << 'EOF'"
  echo "  (paste contents)"
  echo "  EOF"
  exit 1
}

export TZ=Asia/Kolkata
nohup "$PY" scripts/gcp_scheduler_daemon.py >> "$LOG/scheduler.log" 2>&1 &
echo "Scheduler daemon PID: $!"
sleep 4

echo ""
echo "=== Running processes (should be 1 scheduler, 0 listeners) ==="
pgrep -af "gcp_scheduler_daemon|telegram_command_listener" || echo "(none — check scheduler.log)"

bash scripts/diagnose_telegram.sh

echo ""
echo "Telegram commands poll inside scheduler.log (not commands.log)."
echo "Test: send /status in Telegram."
