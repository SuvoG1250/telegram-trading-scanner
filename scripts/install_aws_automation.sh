#!/usr/bin/env bash
# AWS EC2 automation — same scheduler as GCP (Ubuntu VPS, no sudo required)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/install_gcp_automation.sh"
