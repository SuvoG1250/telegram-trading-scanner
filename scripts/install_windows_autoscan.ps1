# NOT RECOMMENDED — causes extra GitHub runs. Use cron-job.org once-daily job instead.
# Run only if you intentionally want local PC to trigger scan_once every 5 min.
# Requires: gh auth login

$ErrorActionPreference = "Stop"
$TaskName = "TelegramTradingBot-AutoScan"
$Repo = "SuvoG1250/telegram-trading-scanner"

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    Write-Host "Install GitHub CLI and run: gh auth login" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction -Execute "gh" -Argument @(
    "workflow", "run", "Auto Trading Bot",
    "--repo", $Repo,
    "-f", "mode=scan_once"
)

# Mon-Fri 9:10 AM - 3:35 PM (covers market 9:15-3:30)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "09:10" -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Hours 6.5)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force `
    -Description "Triggers trading bot scan on GitHub every 5 min during NSE session"

Write-Host "Installed task: $TaskName" -ForegroundColor Green
Write-Host "Runs Mon-Fri 9:10-15:40 IST every 5 minutes."
Write-Host "Test now: gh workflow run `"Auto Trading Bot`" --repo $Repo -f mode=scan_once"
