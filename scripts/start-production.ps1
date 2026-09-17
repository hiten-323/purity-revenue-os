<#
    Bring Purity's production processes up from the ecosystem config.

    WHY THIS EXISTS
    ---------------
    On 2026-09-15 a reboot left nothing running, and `pm2 resurrect` restored
    C:\Users\hiten\.pm2\dump.pm2 -- a snapshot that still carried the legacy
    jules_session cwd and Python 3.11. The API came up serving the wrong
    database (1831 leads instead of 1858) with none of the current safety code.
    It had happened once before.

    A dump is a photograph of one moment. ecosystem.config.js is the intent,
    and it resolves every path from its own __dirname, so it is correct no
    matter where it is invoked from. This script therefore:

      * NEVER calls pm2 resurrect
      * always names the config by absolute path
      * verifies afterwards that the right tree actually came up

    That last point is the one that matters. Starting correctly is easy;
    noticing that you started incorrectly is what took three hours to spot.
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

# pm2 lives in the roaming npm dir; a logon task does not always inherit PATH.
$npm = Join-Path $env:APPDATA 'npm'
if (Test-Path $npm) { $env:PATH = "$npm;$env:PATH" }

# The network stack and the user profile are not always ready the instant a
# logon trigger fires. cloudflared and the Neon-backed voice agent both fail
# noisily when they start too early.
Start-Sleep -Seconds 20

Say "starting from $Config"
$out = & pm2 start $Config 2>&1
$out | ForEach-Object { Say "  pm2: $_" }

Start-Sleep -Seconds 25

# ---- verify the RIGHT tree came up, not merely that something did ----------
$expectedLeads = 1858
try {
    $resp = Invoke-RestMethod -Uri 'http://127.0.0.1:8003/api/v1/b2b/leads?limit=1' -TimeoutSec 45
    $total = $resp.total
    if ($total -eq $expectedLeads) {
        Say "OK: API serving the correct tree ($total leads)"
    } else {
        Say "*** WRONG TREE: API reports $total leads, expected $expectedLeads."
        Say "*** 1831 means the legacy jules_session database. Stop and investigate."
    }
} catch {
    Say "WARN: could not read the lead count ($($_.Exception.Message)). API may still be starting."
}

# The interpreter is the fastest tell that a process came from a stale dump:
# the config pins Python 3.12, the old dump carried 3.11.
try {
    $conn = Get-NetTCPConnection -LocalPort 8003 -State Listen -ErrorAction Stop | Select-Object -First 1
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)"
    if ($proc.CommandLine -match 'Python31[01]') {
        Say "*** WRONG INTERPRETER: $($proc.CommandLine) -- this did not come from the config."
    } else {
        Say "OK: interpreter $($proc.CommandLine)"
    }
} catch {
    Say "WARN: nothing listening on 8003 yet."
}

Say "--- startup complete ---"
