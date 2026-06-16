#!/usr/bin/env bash
# Install user crontab — same schedule as GitHub + cron-job.org (no sudo needed)
set -euo pipefail

HOME_DIR="${HOME:?}"
APP="$HOME_DIR/telegram-trading-scanner"
PY="$APP/venv/bin/python"
LOG="$HOME_DIR/tradingbot-logs"
CRON_FILE=$(mktemp)

mkdir -p "$LOG"
chmod +x "$APP/scripts/telegram_command_listener.py" 2>/dev/null || true

if [[ ! -x "$PY" ]]; then
  echo "Missing venv: $PY — run: cd $APP && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "$APP/.env" ]]; then
  echo "Missing $APP/.env — upload your .env first."
  exit 1
fi

# Keep any existing non-tradingbot crontab lines
crontab -l 2>/dev/null | grep -v "telegram-trading-scanner" | grep -v "tradingbot-logs" > "$CRON_FILE" || true

cat >> "$CRON_FILE" << EOF
# Telegram Trading Bot — GCP automation (IST via TZ)
CRON_TZ=Asia/Kolkata

# Command listener 24/7 (/upstox_token /live /help) — restart daily 6:55 AM
55 6 * * * cd $APP && $PY scripts/telegram_command_listener.py --seconds 86400 --interval 1.5 >> $LOG/commands.log 2>&1

# NSE session Mon-Fri 9:10 IST (~6.5 h, scan every 3 min + BTST)
10 9 * * 1-5 cd $APP && $PY upstox_live_runner.py --max-minutes 390 >> $LOG/nse-session.log 2>&1

# Global BTC/ETH/Gold — 7-8 AM and 4-10 PM IST (58 min each, like GitHub global job)
0 7,8,16,17,18,19,20,21,22 * * * cd $APP && $PY session_runner.py --max-minutes 58 >> $LOG/global-session.log 2>&1
EOF

crontab "$CRON_FILE"
rm -f "$CRON_FILE"

echo "=== Crontab installed ==="
crontab -l
echo ""
echo "Logs: $LOG/"
echo "Start commands NOW (optional):"
echo "  cd $APP && nohup $PY scripts/telegram_command_listener.py --seconds 86400 --interval 1.5 >> $LOG/commands.log 2>&1 &"
echo ""
echo "Disable GitHub cron-job.org jobs to avoid DUPLICATE alerts (run on your PC):"
echo "  python scripts/setup_cron_job_org.py --list"
