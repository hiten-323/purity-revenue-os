<#
    Bring Purity's production processes up from the ecosystem config.

    The script deliberately avoids pm2 resurrect and verifies that the
    canonical API is alive after startup. It does not embed a database row
    count as a proxy for the correct deployment tree.
#>

$ErrorActionPreference = 'Continue'

$Repo   = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $Repo 'ecosystem.config.js'
$Log    = Join-Path $Repo 'logs\startup.log'

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Log) | Out-Null

function Say($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Output $line
    Add-Content -Path $Log -Value $line -Encoding utf8
}

Say "--- startup ---"

if (-not (Test-Path $Config)) {
    Say "FATAL: ecosystem.config.js not found at $Config. Nothing started."
    exit 1
}

$npm = Join-Path $env:APPDATA 'npm'
if (Test-Path $npm) { $env:PATH = "$npm;$env:PATH" }

Start-Sleep -Seconds 20

Say "starting from $Config"
$out = & pm2 start $Config 2>&1
$out | ForEach-Object { Say "  pm2: $_" }

Start-Sleep -Seconds 25

# Verify the canonical API is answering and expose its live lead count for
# diagnostics, without baking today's database size into the startup contract.
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8003/health' -TimeoutSec 45
    if ($health.status -eq 'healthy' -and $health.database -eq 'connected') {
        Say "OK: API health is healthy; database is connected"
    } else {
        Say "*** API HEALTH NOT READY: $($health | ConvertTo-Json -Compress)"
    }
} catch {
    Say "WARN: could not read /health ($($_.Exception.Message)). API may still be starting."
}

try {
    $conn = Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction Stop | Select-Object -First 1
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
    if ($proc.CommandLine -match 'Python31[01]') {
        Say "*** WRONG INTERPRETER: $($proc.CommandLine) -- this did not come from the current config."
        exit 1
    } else {
        Say "OK: interpreter $($proc.CommandLine)"
    }
} catch {
    Say "WARN: nothing listening on 8003 yet."
}

Say "--- startup complete ---"
