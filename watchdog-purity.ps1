# Purity Beans self-healing watchdog — canonical production tree only.
# Health probes may restart a named app, but this script never taskkills ports,
# resurrects an arbitrary PM2 dump, or references the legacy jules_session tree.
$ErrorActionPreference = 'SilentlyContinue'

$Root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Frontend = "$Root\frontend"
$Node     = (Get-Command node -ErrorAction SilentlyContinue).Source
$Pm2      = (Get-Command pm2 -ErrorAction SilentlyContinue).Source
$Npm      = (Get-Command npm -ErrorAction SilentlyContinue).Source
$Log      = "$Root\watchdog.log"

function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -Append -Encoding utf8 $Log }

function Test-Health($url, $timeout = 8) {
    foreach ($try in 1..2) {
        try {
            $c = (Invoke-WebRequest -Uri $url -TimeoutSec $timeout -UseBasicParsing).StatusCode
            if ($c -lt 500) { return $true }
        } catch { }
        if ($try -eq 1) { Start-Sleep -Seconds 4 }
    }
    return $false
}

if (-not (Test-Path "$Root\ecosystem.config.js")) {
    Log "canonical ecosystem missing -> refusing to act"
    exit 1
}

if (-not (Test-Path "$Frontend\.next\BUILD_ID")) {
    Log "frontend .next build missing -> building canonical frontend"
    Push-Location $Frontend
    & $Node $Npm run build *> "$Root\frontend-build.log"
    $buildExit = $LASTEXITCODE
    Pop-Location
    if ($buildExit -ne 0) {
        Log "frontend build failed -> leaving current process untouched"
        exit 1
    }
    & $Node $Pm2 restart purity-beans *> $null
}

# API health is exposed at /health, not /api/v1/health.
if (-not (Test-Health "http://127.0.0.1:8003/health")) {
    Log "API unhealthy -> restart purity-api"
    & $Node $Pm2 restart purity-api --update-env *> $null
    Start-Sleep -Seconds 12
}

$localFront = Test-Health "http://127.0.0.1:3001/dashboard"
if (-not $localFront) {
    Log "frontend unhealthy -> restart purity-beans"
    & $Node $Pm2 restart purity-beans *> $null
    Start-Sleep -Seconds 8
    $localFront = Test-Health "http://127.0.0.1:3001/dashboard"
}

if ($localFront) {
    $public = Test-Health "https://dashboard.p3online.in/dashboard" 15
    if (-not $public) {
        $stamp = "$Root\.tunnel-restart"
        $recent = (Test-Path $stamp) -and ((Get-Date) - (Get-Item $stamp).LastWriteTime).TotalMinutes -lt 5
        if ($recent) {
            Log "public down but tunnel restarted <5 min ago -> waiting"
        } else {
            Log "public down but origin up -> restart purity-tunnel"
            & $Node $Pm2 restart purity-tunnel *> $null
            Set-Content -Path $stamp -Value (Get-Date -Format o) -Encoding utf8
        }
    }
}
