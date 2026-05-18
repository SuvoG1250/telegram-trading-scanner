# Trigger Auto Trading Bot on GitHub (full session). Requires: gh auth login
param(
    [int]$MaxMinutes = 390
)

$ErrorActionPreference = "Stop"
$repo = "SuvoG1250/telegram-trading-scanner"

Write-Host "Triggering Auto Trading Bot ($MaxMinutes min) on $repo ..."
gh workflow run "Auto Trading Bot" --repo $repo -f "max_minutes=$MaxMinutes"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Start-Sleep -Seconds 3
gh run list --repo $repo --workflow "Auto Trading Bot" --limit 1
Write-Host "Check Telegram for: Auto Trading Bot — RUNNING"
