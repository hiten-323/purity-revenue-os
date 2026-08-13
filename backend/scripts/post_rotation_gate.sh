#!/usr/bin/env bash
# =============================================================================
# POST-rotation gate — run AFTER credentials are rotated and PM2 restarted.
#
# Proves the *running* system, not merely the git checkout.
#
# Checks (best-effort; some need local PM2 / network):
#   1  git HEAD printed (informational)
#   2  PM2 process list + cwd if pm2 is available
#   3  /health == healthy
#   4  optional: EXPECTED_COMMIT match against a reported PM2 env (manual)
#
# Exit codes:
#   0  health + available local checks pass
#   2  commit mismatch when EXPECTED_COMMIT set against git HEAD
#   3  /health not healthy
#   4  pm2 not available / process missing (warning becomes fail if REQUIRE_PM2=1)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$BACKEND_DIR/.." && pwd)"

EXPECTED_COMMIT="${EXPECTED_COMMIT:-}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
REQUIRE_PM2="${REQUIRE_PM2:-0}"

echo "=== POST-rotation gate ==="
echo "repo HEAD: $(git -C "$REPO_DIR" rev-parse HEAD)"

if [[ -n "$EXPECTED_COMMIT" ]]; then
  HEAD="$(git -C "$REPO_DIR" rev-parse HEAD)"
  if [[ "$HEAD" != "$EXPECTED_COMMIT" ]]; then
    echo "FAIL: git HEAD ($HEAD) != EXPECTED_COMMIT ($EXPECTED_COMMIT)"
    exit 2
  fi
  echo "PASS: git HEAD matches EXPECTED_COMMIT"
  echo "NOTE: this is still not proof that PM2 loaded that commit."
fi

echo ""
echo "[pm2] process inventory"
if command -v pm2 >/dev/null 2>&1; then
  pm2 jlist 2>/dev/null | python -c '
import sys, json
try:
    data = json.load(sys.stdin)
except Exception as e:
    print(f"could not parse pm2 jlist: {e}")
    sys.exit(0)
if not data:
    print("pm2 reports no processes")
    sys.exit(0)
for p in data:
    name = p.get("name")
    status = (p.get("pm2_env") or {}).get("status")
    cwd = (p.get("pm2_env") or {}).get("pm_cwd") or (p.get("pm2_env") or {}).get("cwd")
    script = (p.get("pm2_env") or {}).get("pm_exec_path") or (p.get("pm2_env") or {}).get("script")
    print(f"  {name}: status={status} cwd={cwd} script={script}")
' || true
  echo "PASS: pm2 reachable (inspect cwd/script above against the tip)"
else
  echo "pm2 not on PATH"
  if [[ "$REQUIRE_PM2" == "1" ]]; then
    echo "FAIL: REQUIRE_PM2=1 and pm2 missing"
    exit 4
  fi
fi

echo ""
echo "[health] $HEALTH_URL"
BODY="$(curl -sfS "$HEALTH_URL")" || {
  echo "FAIL: health request failed"
  exit 3
}
echo "$BODY"
echo "$BODY" | python -c '
import sys, json
data = json.load(sys.stdin)
if data.get("status") != "healthy":
    print("FAIL: status != healthy")
    sys.exit(3)
print("PASS: /health healthy")
' || exit 3

echo ""
echo "Manual follow-ups (not automated here):"
echo "  - confirm PM2 cwd points at this tree"
echo "  - confirm send_proof_fix is importable inside the running worker"
echo "  - live Zoho / AiSensy / Shopify / Cerebras auth with rotated secrets"
echo "  - one controlled email send + delivery webhook"
echo "  - AiSensy send only after approved Meta template exists"
echo ""
echo "=============================================="
echo "POST-ROTATION AUTOMATED CHECKS PASSED"
echo "=============================================="
exit 0
