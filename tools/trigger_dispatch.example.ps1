# Triggers the GitHub Actions workflow via repository_dispatch (same as cron-job.org).
#
# Setup:
#   1. GitHub → Settings → Developer settings → PAT (classic) with "repo" scope.
#   2. Copy this file to trigger_dispatch.ps1 (gitignored). Edit $Owner if needed.
#   3. Set env var GITHUB_DISPATCH_PAT to the token (never commit the token).
#   4. Windows Task Scheduler: daily 05:00, timezone Addis Ababa (or your choice):
#      Program: powershell.exe
#      Arguments: -ExecutionPolicy Bypass -File "C:\path\to\nsda-quran-bot\tools\trigger_dispatch.ps1"
#      Optional: set GITHUB_DISPATCH_PAT in Task "Actions" → create/update env for the task
#        (or store token in Windows Credential Manager and read it in script — advanced).

param(
    [string]$Owner = "YOUR_GITHUB_USERNAME",
    [string]$Repo = "nsda-quran-bot"
)

$token = $env:GITHUB_DISPATCH_PAT
if (-not $token) {
    Write-Error "Set environment variable GITHUB_DISPATCH_PAT to a GitHub PAT (classic: repo scope)."
    exit 1
}

$uri = "https://api.github.com/repos/$Owner/$Repo/dispatches"
$headers = @{
    Accept                 = "application/vnd.github+json"
    Authorization          = "Bearer $token"
    "X-GitHub-Api-Version" = "2022-11-28"
}

Invoke-RestMethod -Uri $uri -Method Post -Headers $headers `
    -Body '{"event_type":"daily_quran_post"}' -ContentType "application/json"

Write-Host "OK: repository_dispatch sent. Check GitHub Actions for a new run."
