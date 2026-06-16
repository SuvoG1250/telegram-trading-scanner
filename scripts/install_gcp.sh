#!/usr/bin/env bash
# One-time GCP VM setup for Telegram Trading Bot
# Run on Ubuntu after: git clone ... && cd telegram-trading-scanner

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"
HOME_DIR="$(eval echo "~$USER_NAME")"
APP_DIR="$HOME_DIR/telegram-trading-scanner"

echo "=== Telegram Trading Bot — GCP install ==="
echo "Repo: $REPO_DIR"
echo "User: $USER_NAME"

sudo timedatectl set-timezone Asia/Kolkata || true

sudo apt-get update -qq
sudo apt-get install -y git python3 python3-venv python3-pip

if [[ "$REPO_DIR" != "$APP_DIR" ]] && [[ ! -d "$APP_DIR/.git" ]]; then
  echo "Cloning to $APP_DIR ..."
  git clone https://github.com/SuvoG1250/telegram-trading-scanner.git "$APP_DIR"
fi

cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ""
  echo ">>> Edit .env with your secrets before starting services:"
  echo "    nano $APP_DIR/.env"
  echo ""
fi

mkdir -p data
./venv/bin/python scripts/telegram_delete_webhook.py || true

# systemd — substitute user and home paths
for unit in tradingbot-commands tradingbot-session; do
  sed "s|%i|$USER_NAME|g; s|%h|$HOME_DIR|g" \
    "$APP_DIR/deploy/${unit}.service" | sudo tee "/etc/systemd/system/${unit}.service" > /dev/null
done
sed "s|%i|$USER_NAME|g; s|%h|$HOME_DIR|g" \
  "$APP_DIR/deploy/tradingbot-session.timer" | sudo tee /etc/systemd/system/tradingbot-session.timer > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable tradingbot-commands.service
sudo systemctl enable tradingbot-session.timer

echo ""
echo "=== Install done ==="
echo "1) nano $APP_DIR/.env   (TELEGRAM_TOKEN, TELEGRAM_GROUP_CHAT_ID, UPSTOX_*, etc.)"
echo "2) sudo systemctl start tradingbot-commands.service"
echo "3) sudo systemctl start tradingbot-session.timer"
echo "4) Test: send /help in Telegram"
echo ""
echo "Optional: disable GitHub cron-job.org jobs to avoid duplicate alerts."
echo "  python scripts/setup_cron_job_org.py --list"
