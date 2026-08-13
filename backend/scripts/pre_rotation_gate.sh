#!/usr/bin/env bash
# =============================================================================
# PRE-rotation gate — credential rotation may proceed only on exit 0.
#
# This does NOT prove PM2 is running this tip. Git HEAD == tip only proves the
# checkout is current. PM2 cwd/commit, /health, and live provider auth belong
# in post_rotation_gate.sh AFTER credentials are rotated and the process is
# restarted against the new secrets.
#
# Checks:
#   1  full pytest suite
#   2  send-proof listener: strict on, old off
#   3  dry-run A/B/C fail-closed contracts
#   4  secret scan of tracked tip files
#   5  no .env / *.db / lead exports tracked
#
# Exit codes:
#   0   all pass — rotate credentials
#   1   listener / hygiene / secret scan failure
#  10–14 dry-run contract failure (see dryrun_failclosed.py)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$BACKEND_DIR/.." && pwd)"

SKIP_PYTEST="${SKIP_PYTEST:-0}"

cd "$BACKEND_DIR"
echo "=== PRE-rotation gate ==="
echo "repo:    $REPO_DIR"
echo "backend: $BACKEND_DIR"
echo "HEAD:    $(git -C "$REPO_DIR" rev-parse HEAD)"
echo ""
echo "Scope: source contracts only. Not a PM2 / health / provider check."
echo ""

# ── 1. Test suite ──────────────────────────────────────────────────────────
if [[ "$SKIP_PYTEST" != "1" ]]; then
  echo "[1/5] pytest"
  if command -v pytest >/dev/null 2>&1; then
    python -m pytest tests/ -v --tb=short
  else
    echo "pytest not on PATH; falling back to unittest discover"
    python -m unittest discover -s tests -v
  fi
  echo "PASS: test suite"
else
  echo "[1/5] pytest SKIPPED (SKIP_PYTEST=1)"
fi

# ── 2. Listener ────────────────────────────────────────────────────────────
echo ""
echo "[2/5] send_proof listener"
python "$SCRIPT_DIR/check_send_proof_listener.py"
# script exits non-zero on fail; set -e will stop us

# ── 3. Dry-run fail-closed ─────────────────────────────────────────────────
echo ""
echo "[3/5] dry-run fail-closed (A/B/C)"
set +e
python "$SCRIPT_DIR/dryrun_failclosed.py"
RC=$?
set -e
if [[ $RC -ne 0 ]]; then
  echo "FAIL: dry-run exit $RC"
  exit "$RC"
fi

# ── 4 + 5. Secret scan + tracked-file hygiene ──────────────────────────────
echo ""
echo "[4/5] secret scan (tip files)"
echo "[5/5] no .env / *.db / lead exports tracked"
python "$SCRIPT_DIR/check_repo_hygiene.py"

echo ""
echo "=============================================="
echo "PRE-ROTATION GATES PASSED"
echo "Next: rotate the eight live credentials."
echo "Then: bash backend/scripts/post_rotation_gate.sh"
echo "=============================================="
exit 0
