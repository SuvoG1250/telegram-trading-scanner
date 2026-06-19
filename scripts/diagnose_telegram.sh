#!/usr/bin/env bash
# Diagnose Telegram getUpdates conflicts on GCP
set -euo pipefail

APP="${HOME}/telegram-trading-scanner"
PY="${APP}/venv/bin/python"
cd "$APP" 2>/dev/null || { echo "Missing $APP"; exit 1; }

echo "=== Telegram poller processes ==="
pgrep -af "telegram_command_listener|gcp_scheduler_daemon|poll_telegram" || echo "(none)"

echo ""
echo "=== .env Telegram keys (masked) ==="
for key in TELEGRAM_TOKEN TELEGRAM_CHAT_ID TELEGRAM_GROUP_CHAT_ID TELEGRAM_COMMANDS_ENABLED TELEGRAM_POLL_IN_SESSION; do
  val=$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)
  if [[ -z "$val" ]]; then
    echo "$key=(not set)"
  elif [[ "$key" == *TOKEN* ]]; then
    echo "$key=set (${#val} chars)"
  else
    echo "$key=$val"
  fi
done

echo ""
echo "=== Python config check ==="
"$PY" -c "
from config import telegram_commands_status, TELEGRAM_CHAT_ID, telegram_chat_ids
ok, msg = telegram_commands_status()
print('telegram_commands_status:', ok, msg)
print('chat_ids:', telegram_chat_ids())
"

echo ""
echo "=== Poll lock file ==="
ls -la data/telegram_poll.lock 2>/dev/null || echo "(no lock file)"
cat data/telegram_poll.lock 2>/dev/null || true

echo ""
echo "=== Recent commands log ==="
tail -15 "${HOME}/tradingbot-logs/commands.log" 2>/dev/null || echo "(no commands.log — polling is in scheduler.log now)"

echo ""
echo "=== Recent scheduler log ==="
tail -15 "${HOME}/tradingbot-logs/scheduler.log" 2>/dev/null || echo "(no scheduler.log)"

echo ""
echo "=== Fix (run on GCP) ==="
echo "  pkill -f telegram_command_listener"
echo "  pkill -f gcp_scheduler_daemon"
echo "  sleep 2"
echo "  bash scripts/install_gcp_automation.sh"
echo ""
echo "Also stop any bot running on your Windows PC or cron-job.org using the same TELEGRAM_TOKEN."
