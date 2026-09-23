# Apply sqlite-lock + trust-confidence fixes on LIVE Windows checkout.
# Run from PowerShell:
#   cd C:\Users\hiten\Desktop\ppp\claude\CODE\purity-revenue-os
#   powershell -ExecutionPolicy Bypass -File scripts\apply_sqlite_trust_fixes.ps1
#
# Keeps AISENSY_ENABLED=0. Does not place dials or mass-email.
# Restarts purity-api and purity-worker so WAL/busy_timeout + patches load.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "backend\app\database\database.py"))) {
  $Root = "C:\Users\hiten\Desktop\ppp\claude\CODE\purity-revenue-os"
}
Set-Location $Root
Write-Host "LIVE ROOT: $Root"

git fetch origin fix/sqlite-locks-trust-confidence 2>$null
$branch = "fix/sqlite-locks-trust-confidence"
Write-Host "current branch: $(git rev-parse --abbrev-ref HEAD)"

$status = git status --porcelain
if ($status) {
  Write-Host "Working tree dirty — listing status (will checkout files from origin/$branch):"
  Write-Host $status
}

git checkout -B $branch
git merge --ff-only "origin/$branch" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "ff-only failed; checking out key files from origin/$branch"
  git checkout "origin/$branch" -- `
    backend/app/database/database.py `
    backend/app/services/trust_confidence.py `
    backend/app/services/call_db_lock_patch.py `
    backend/app/purity_boot_patches.py `
    backend/scripts/trust_confidence_sweep.py `
    backend/tests/test_sqlite_lock_retry.py `
    backend/tests/test_confidence_backfill.py `
    scripts/apply_sqlite_trust_fixes.ps1
  git add -A
  git -c user.email="hiten@local" -c user.name="hiten" commit -m "apply sqlite lock + trust confidence fixes from origin/$branch" 2>$null
}

$Intel = "C:\Users\hiten\Desktop\ppp\purity-revenue-os-intel"
if (Test-Path $Intel) {
  Write-Host "Mirroring critical files into $Intel"
  foreach ($rel in @(
    "backend\app\database\database.py",
    "backend\app\services\trust_confidence.py",
    "backend\app\services\call_db_lock_patch.py",
    "backend\app\purity_boot_patches.py",
    "backend\scripts\trust_confidence_sweep.py"
  )) {
    $src = Join-Path $Root $rel
    $dst = Join-Path $Intel $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
    Copy-Item -Force $src $dst
  }
}

Set-Location (Join-Path $Root "backend")
Write-Host "=== BEFORE counts ==="
python -m scripts.trust_confidence_sweep --counts-only

Write-Host "=== Running limited trust confidence sweep (limit=80) ==="
python -m scripts.trust_confidence_sweep --limit 80

Write-Host "=== AFTER counts ==="
python -m scripts.trust_confidence_sweep --counts-only

Set-Location $Root
Write-Host "=== Restarting purity-api, purity-worker ==="
pm2 restart purity-api purity-worker
Start-Sleep -Seconds 8
pm2 list

Write-Host "=== Recent lock / trust lines ==="
pm2 logs purity-outreach --lines 80 --nostream 2>$null | Select-String -Pattern "database is locked" | Select-Object -Last 20
pm2 logs purity-worker --lines 60 --nostream 2>$null | Select-String -Pattern "database is locked|confidence sweep|trust sweep" | Select-Object -Last 20

Write-Host "DONE. AISENSY untouched (must stay 0). Smart/Auto/AI calling not disabled."
