# Purity Beans self-healing watchdog
# Runs every couple minutes (scheduled task) and after wake-from-sleep. pm2
# reports crashed/wedged processes as "online" (a hung API still holds its port;
# a dropped tunnel keeps running) so pm2 never restarts them. This checks ACTUAL
# health over HTTP and restarts only what is genuinely unhealthy.
$ErrorActionPreference = 'SilentlyContinue'

$Root     = "C:\Users\hiten\Desktop\ppp\claude\CODE\purity_beans_ai\jules_session"
$Frontend = "$Root\frontend"
# Call node directly, NOT the .cmd shims. PowerShell invoking a .cmd makes
# Windows spawn cmd.exe, and that child console is NOT covered by the task's
# -WindowStyle Hidden. With this task firing every 60 seconds, that produced a
# terminal window blinking on the desktop once a minute, all day. The .cmd
# files are only batch wrappers around these node entrypoints.
$Node     = "C:\Program Files\nodejs\node.exe"
$Pm2      = "C:\Users\hiten\AppData\Roaming\npm\node_modules\pm2\bin\pm2"
$Npm      = "C:\Program Files\nodejs\node_modules\npm\bin\npm-cli.js"
$Log      = "$Root\watchdog.log"

function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -Append -Encoding utf8 $Log }

# Healthy = any HTTP response < 500 within timeout. Retg once to avoid acting on
# a transient blip. Returns $true/$false.
function Test-Health($url, $timeout = 8) {
    foreach ($try in 1..2) {
        try {
            $c = (Invoke-WebRequest -Uri $url -TimeoutSec $timeout -UseBasicParsing).StatusCode
            if ($c -lt 500) { return $true }
        } catch { }
        Start-Sleep -Seconds 4
    }
    return $false
}

# 1. Frontend production build must exist, else `next start` crash-loops -> 502.
if (-not (Test-Path "$Frontend\.next\BUILD_ID")) {
    Log "frontend .next build missing -> building"
    Push-Location $Frontend; & $Node $Npm run build *> $null; Pop-Location
    & $Node $Pm2 restart purity-beans *> $null
}

# 2. API — probe /docs (no DB/Redis, so a slow Redis ping can't false-alarm).
# Port 8003, not 8001. The API moved and this probe did not, so every run
# found nothing listening, declared the API dead and restarted it — once a
# minute, all day, 800+ restarts deep. A health check pointed at the wrong port
# does not detect an outage, it manufactures one.
#
# /health, not /docs. /docs is cheap (1 KB, 9 ms) so cost was never the issue —
# it just proves FastAPI is serving routes. An API that has lost its database
# answers /docs perfectly while every real request fails, so the watchdog would
# report healthy straight through a total outage. /health touches the DB and
# returns 503 when it cannot, which Test-Health treats as a failure.
if (-not (Test-Health "http://127.0.0.1:8003/api/v1/health")) {
    Log "API unhealthy -> restart purity-api"
    & $Node $Pm2 restart purity-api *> $null
    Start-Sleep -Seconds 12
}

# 3. Frontend origin.
$localFront = Test-Health "http://127.0.0.1:3001/dashboard"
if (-not $localFront) {
    Log "frontend unhealthy -> restart purity-beans"
    & $Node $Pm2 restart purity-beans *> $null
    Start-Sleep -Seconds 8
    $localFront = Test-Health "http://127.0.0.1:3001/dashboard"
}

# 4. Tunnel — if the origin is healthy locally but the public URL is down, the
#    Cloudflare tunnel lost its edge connection (classic post-sleep symptom).
#    Restart the pm2 tunnel (a cloudflared we control without admin).
if ($localFront) {
    $public = Test-Health "https://dashboard.p3online.in/dashboard" 15
    if (-not $public) {
        # Do not restart a tunnel we only just restarted. This task fires every
        # 60s and cloudflared needs ~15s to re-establish its edge connection,
        # so the next check kept catching it mid-reconnect and restarting it
        # again — the watchdog was CAUSING the outage it was detecting, in a
        # loop that ran all day. One restart, then five minutes to prove it.
        $stamp = "$Root\.tunnel-restart"
        $recent = (Test-Path $stamp) -and
                  ((Get-Date) - (Get-Item $stamp).LastWriteTime).TotalMinutes -lt 5
        if ($recent) {
            Log "public down but tunnel restarted <5 min ago -> waiting, not restarting"
        } else {
            Log "public down but origin up -> restart purity-tunnel"
            & $Node $Pm2 restart purity-tunnel *> $null
            Set-Content -Path $stamp -Value (Get-Date -Format o) -Encoding utf8
        }
    }
}
