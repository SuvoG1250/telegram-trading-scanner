# Setup cron-job.org (Option B) — run from project folder after adding keys to .env
# Requires: CRONJOB_API_KEY and GITHUB_PAT in .env

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".env")) {
    Write-Host @"

Create .env with:

  CRONJOB_API_KEY=your_key_from_console.cron-job.org_settings
  GITHUB_PAT=ghp_your_github_token_with_repo_scope

Then run this script again.

"@ -ForegroundColor Yellow
    if (-not (Test-Path ".env.example")) {
        @"
# cron-job.org API key: https://console.cron-job.org/settings
CRONJOB_API_KEY=

# GitHub classic PAT with repo scope: https://github.com/settings/tokens
GITHUB_PAT=
"@ | Set-Content ".env.example" -Encoding UTF8
        Write-Host "Created .env.example — copy to .env and fill in values." -ForegroundColor Cyan
    }
    exit 1
}

python scripts/setup_cron_job_org.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`nTesting one GitHub scan..." -ForegroundColor Cyan
python scripts/setup_cron_job_org.py --test
