<#
.SYNOPSIS
  Read-only production-readiness checklist for the Windows host.

.DESCRIPTION
  Does NOT start PM2, does NOT dial, does NOT send mail/WhatsApp, and does NOT
  mutate ecosystem.config.js or backend/.env. Prints PASS / FAIL / UNVERIFIED
  for each check so a human can decide whether the host is ready.

  Usage (from repo root):
    powershell -ExecutionPolicy Bypass -File scripts\production_readiness_windows.ps1
#>

$ErrorActionPreference = 'Continue'
$Repo = Split-Path -Parent $PSScriptRoot
$BackendEnv = Join-Path $Repo 'backend\.env'
$Ecosystem = Join-Path $Repo 'ecosystem.config.js'
$Results = @()

function Record([string]$Id, [string]$Status, [string]$Detail) {
    $script:Results += [pscustomobject]@{ Id = $Id; Status = $Status; Detail = $Detail }
    $color = switch ($Status) {
        'PASS' { 'Green' }
        'FAIL' { 'Red' }
        default { 'Yellow' }
    }
    Write-Host ("[{0}] {1}: {2}" -f $Status, $Id, $Detail) -ForegroundColor $color
}

Write-Host "=== Purity production readiness (READ-ONLY) ===" -ForegroundColor Cyan
Write-Host "Repo: $Repo"
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') IST-label-local"
Write-Host ""

# A -- ecosystem present
if (Test-Path $Ecosystem) {
    Record 'A-ecosystem' 'PASS' "found $Ecosystem"
} else {
    Record 'A-ecosystem' 'FAIL' "ecosystem.config.js missing"
}

# B -- backend/.env present (contents not printed)
if (Test-Path $BackendEnv) {
    Record 'B-env-file' 'PASS' 'backend/.env exists (secrets not shown)'
} else {
    Record 'B-env-file' 'FAIL' 'backend/.env missing'
}

# C-F -- production executor policy: SMART off, AUTO on; WhatsApp/calling remain off
if (Test-Path $Ecosystem) {
    $eco = Get-Content $Ecosystem -Raw
    foreach ($pair in @(
        @{ Id = 'C-smart-outreach-off'; Needle = 'SMART_OUTREACH_ENABLED: "0"' },
        @{ Id = 'D-auto-outreach-on'; Needle = 'AUTO_OUTREACH_ENABLED: "1"' },
        @{ Id = 'E-aisensy-off'; Needle = 'AISENSY_ENABLED: "0"' },
        @{ Id = 'F-ai-calling-off'; Needle = 'AI_CALLING_ENABLED: "0"' }
    )) {
        if ($eco -match [regex]::Escape($pair.Needle)) {
            Record $pair.Id 'PASS' $pair.Needle
        } else {
            Record $pair.Id 'FAIL' "expected $($pair.Needle)"
        }
    }
    if ($eco -match 'AI_CALLING_ENABLED:\s*"1"' -or $eco -match 'AISENSY_ENABLED:\s*"1"') {
        Record 'G-no-armed-defaults' 'FAIL' 'ecosystem arms calling or WhatsApp by default'
    } else {
        Record 'G-no-armed-defaults' 'PASS' 'no AI_CALLING/AISENSY =1 defaults in ecosystem'
    }
}

# H -- personal_test_call allowlist
$ptc = Join-Path $Repo 'backend\scripts\personal_test_call.py'
if (Test-Path $ptc) {
    $src = Get-Content $ptc -Raw
    if ($src -match '9855593323' -and $src -match 'ALLOWED_TAILS' -and $src -match 'i-am-the-founder') {
        Record 'H-personal-test-guard' 'PASS' 'allowlist + founder flags present'
    } else {
        Record 'H-personal-test-guard' 'FAIL' 'personal_test_call.py guard incomplete'
    }
} else {
    Record 'H-personal-test-guard' 'FAIL' 'personal_test_call.py missing'
}

# I -- Nuraveda sidecar path (existence only)
$candidates = @()
if ($env:NURAVEDA_DIR) { $candidates += $env:NURAVEDA_DIR }
$candidates += @(
    (Join-Path (Split-Path $Repo -Parent) 'ai-voice-agent'),
    (Join-Path (Split-Path $Repo -Parent) 'ai-voice-agent-purity'),
    (Join-Path $Repo 'ai-voice-agent')
)
$found = $null
foreach ($c in $candidates) {
    if (-not $c) { continue }
    $server = Join-Path $c 'src\server.js'
    $agent = Join-Path $c 'src\livekit-agent.js'
    if ((Test-Path $server) -or (Test-Path $agent)) { $found = $c; break }
}
if ($found) {
    Record 'I-nuraveda-cwd' 'PASS' "sidecar at $found"
} else {
    Record 'I-nuraveda-cwd' 'UNVERIFIED' 'NURAVEDA_DIR not found on this host -- set before pm2 start'
}

# J -- pm2 present?
try {
    $pm2 = & pm2 -v 2>$null
    if ($LASTEXITCODE -eq 0 -or $pm2) {
        Record 'J-pm2' 'PASS' "pm2 $pm2"
    } else {
        Record 'J-pm2' 'UNVERIFIED' 'pm2 not on PATH'
    }
} catch {
    Record 'J-pm2' 'UNVERIFIED' 'pm2 not on PATH'
}

# K -- API health (if listening)
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8003/health' -TimeoutSec 5
    if ($health.status -eq 'healthy') {
        Record 'K-api-health' 'PASS' 'API /health healthy'
    } else {
        Record 'K-api-health' 'UNVERIFIED' ($health | ConvertTo-Json -Compress)
    }
} catch {
    Record 'K-api-health' 'UNVERIFIED' 'API not answering on :8003 (may be stopped -- OK for read-only check)'
}

# L -- LiveKit / voice process (host-only)
try {
    $list = & pm2 jlist 2>$null | ConvertFrom-Json
    $voice = @($list | Where-Object { $_.name -match 'nuraveda|voice' })
    if ($voice.Count -gt 0) {
        $online = @($voice | Where-Object { $_.pm2_env.status -eq 'online' }).Count
        Record 'L-voice-pm2' 'UNVERIFIED' "$online/$($voice.Count) voice apps online (DNS/audio not proven)"
    } else {
        Record 'L-voice-pm2' 'UNVERIFIED' 'no nuraveda/voice pm2 apps listed'
    }
} catch {
    Record 'L-voice-pm2' 'UNVERIFIED' 'could not query pm2 process list'
}

# M -- personal live call
Record 'M-personal-live-call' 'UNVERIFIED' 'not executed by this script; run personal_test_call.py manually'

# N -- Windows host end-to-end
Record 'N-windows-e2e' 'UNVERIFIED' 'full Windows runtime (LiveKit DNS, SIP trunk, audio) not verified here'

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Cyan
$Results | Format-Table -AutoSize
$fail = @($Results | Where-Object Status -eq 'FAIL').Count
$unv = @($Results | Where-Object Status -eq 'UNVERIFIED').Count
$pass = @($Results | Where-Object Status -eq 'PASS').Count
Write-Host "PASS=$pass FAIL=$fail UNVERIFIED=$unv"
if ($fail -gt 0) { exit 1 }
exit 0
