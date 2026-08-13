#!/usr/bin/env bash
# Pre-rotation gate. Exit 0 only when every check passes.
#
# Exit codes:
#   0  all pass — credential rotation may proceed
#   1  general / listener failure
#   2  commit mismatch (set EXPECTED_COMMIT)
#   3  /health not healthy
#  10–14  dry-run fail-closed contract failures (see dryrun_failclosed.py)
#
# Usage (from repo root or backend/):
#   EXPECTED_COMMIT=<sha> HEALTH_URL=http://127.0.0.1:8000/health \
#     bash backend/scripts/pre_rotation_gate.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$BACKEND_DIR/.." && pwd)"

EXPECTED_COMMIT="${EXPECTED_COMMIT:-}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
SKIP_HEALTH="${SKIP_HEALTH:-0}"
SKIP_COMMIT="${SKIP_COMMIT:-0}"
SKIP_PYTEST="${SKIP_PYTEST:-0}"

cd "$BACKEND_DIR"
echo "=== pre-rotation gate ==="
echo "backend: $BACKEND_DIR"

# ── 1. Test suite ──────────────────────────────────────────────
if [[ "$SKIP_PYTEST" != "1" ]]; then
  echo ""
  echo "[1/5] pytest"
  if command -v pytest >/dev/null 2>&1; then
    python -m pytest tests/ -v --tb=short
  else
    echo "pytest not found; running unittest discover"
    python -m unittest discover -s tests -v
  fi
  echo "PASS: test suite"
else
  echo "[1/5] pytest SKIPPED (SKIP_PYTEST=1)"
fi

# ── 2. send_proof listener ─────────────────────────────────────
echo ""
echo "[2/5] send_proof_fix listener"
python "$SCRIPT_DIR/check_send_proof_listener.py"
echo "PASS: listener"

# ── 3. Commit match ────────────────────────────────────────────
echo ""
echo "[3/5] commit match"
if [[ "$SKIP_COMMIT" == "1" ]]; then
  echo "SKIPPED (SKIP_COMMIT=1)"
elif [[ -z "$EXPECTED_COMMIT" ]]; then
  echo "EXPECTED_COMMIT not set — printing HEAD only (not failing)"
  git -C "$REPO_DIR" rev-parse HEAD
else
  HEAD="$(git -C "$REPO_DIR" rev-parse HEAD)"
  echo "HEAD=$HEAD"
  echo "EXPECTED=$EXPECTED_COMMIT"
  if [[ "$HEAD" != "$EXPECTED_COMMIT" ]]; then
    echo "FAIL: stale tree (commit mismatch)"
    exit 2
  fi
  echo "PASS: commit matches"
fi

# ── 4. Health ──────────────────────────────────────────────────
echo ""
echo "[4/5] /health"
if [[ "$SKIP_HEALTH" == "1" ]]; then
  echo "SKIPPED (SKIP_HEALTH=1)"
else
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
fi

# ── 5. Dry-run fail-closed contracts ───────────────────────────
echo ""
echo "[5/5] dry-run fail-closed"
python "$SCRIPT_DIR/dryrun_failclosed.py"
RC=$?
if [[ $RC -ne 0 ]]; then
  echo "FAIL: dry-run exit $RC"
  exit "$RC"
fi

echo ""
echo "=========================================="
echo "ALL GATES PASSED — credential rotation OK"
echo "=========================================="
exit 0
