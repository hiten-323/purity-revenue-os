#!/usr/bin/env bash
set -euo pipefail
LOG="/opt/purity-revenue-os/purity-revenue-os/backend/logs/arm_b2b_call_worker_20261110.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "[$(date '+%F %T %Z')] B2B voice-worker preflight started"
if [[ "$(date +%F)" < "2026-11-10" ]]; then
  echo "Date guard: before 2026-11-10; no change made."
  exit 0
fi
cd /opt/purity-revenue-os/purity-revenue-os/backend
# The backend .env contains unquoted values with spaces; never source it as shell.
# Parse it with python-dotenv and export only the allow-list needed by PM2.
OUTREACH_ALLOW_LIST="$(.venv/bin/python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("OUTREACH_ALLOW_LIST", ""))')"
if [[ -z "$OUTREACH_ALLOW_LIST" ]]; then
  echo "BLOCKED: OUTREACH_ALLOW_LIST is missing; worker remains disarmed."
  exit 20
fi
export OUTREACH_ALLOW_LIST

# Preflight is fail-closed: a founder approval alone cannot validate a scraped phone.
PYTHONPATH=. .venv/bin/python - <<'PY'
from datetime import datetime
import os
import re
from dotenv import load_dotenv
load_dotenv(".env", override=True)
from app.database.database import SessionLocal
from app.services.ai_call_cycle import select_call_candidates
from app.services.contact_eligibility import check_voice_eligibility
from app.services.call_intelligence.volume import dial_budget
from app.services import voice_router
from app.services.founder_call_pipeline import call_hours_open
from app.services.business_hours import IST

now = datetime.now(IST)
if now.weekday() == 6 or not call_hours_open(now=now)[0]:
    raise SystemExit("BLOCKED: voice calling window is closed")
if voice_router.kill_switch_engaged():
    raise SystemExit("BLOCKED: global AI calling kill switch is engaged")
ok, detail = voice_router.config_status()
if not ok:
    raise SystemExit("BLOCKED: voice provider is not ready: " + detail)

# TRAI gate for promotional AI/robocalls: require 140-series caller ID,
# PE/TM registration, pre-declaration and verified DCA preference scrubbing.
caller_digits = re.sub(r"\D", "", os.getenv("TRAI_PROMOTIONAL_CALLER_ID", ""))
if not re.fullmatch(r"(?:140\d{7}|91140\d{7})", caller_digits):
    raise SystemExit("BLOCKED: promotional AI calls require a registered 140-series caller ID")
if os.getenv("TRAI_PROMOTIONAL_CALLING_COMPLIANT") != "1":
    raise SystemExit("BLOCKED: TRAI promotional calling compliance has not been verified")
if not os.getenv("TRAI_DLT_PE_ID") or not os.getenv("TRAI_DLT_TELEMARKETER_ID"):
    raise SystemExit("BLOCKED: DLT Principal Entity and Telemarketer IDs are missing")
if os.getenv("TRAI_AUTODIALER_DECLARED") != "1":
    raise SystemExit("BLOCKED: auto-dialer/robocall use has not been declared to the originating access provider")
if os.getenv("TRAI_DCA_PREFERENCE_SCRUBBING_VERIFIED") != "1":
    raise SystemExit("BLOCKED: DCA/DND preference scrubbing is not verified")

recording_backend = os.getenv("RECORDING_BACKEND", "").lower()
recording_bucket = os.getenv("RECORDING_BUCKET", "").strip()
if recording_backend == "r2":
    recording_ready = bool(recording_bucket and os.getenv("R2_ACCOUNT_ID") and os.getenv("R2_ACCESS_KEY_ID") and os.getenv("R2_SECRET_ACCESS_KEY"))
elif recording_backend == "s3":
    recording_ready = bool(recording_bucket and os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY") and os.getenv("AWS_REGION"))
elif recording_backend == "gcp":
    recording_ready = bool(recording_bucket and os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON"))
else:
    recording_ready = False
if not recording_ready:
    raise SystemExit("BLOCKED: recording object storage backend, bucket and credentials are not configured")
db = SessionLocal()
try:
    budget = dial_budget(db)
    candidates = select_call_candidates(db, limit=100)
    eligible = []
    for lead in candidates:
        ok, reason = check_voice_eligibility(lead, db=db, now=now)
        verified = bool(getattr(lead, "phone_verified", False)) or (
            (getattr(lead, "phone_trust", "") or "").strip().upper() == "VERIFIED"
        )
        if ok and verified:
            eligible.append(lead)
    if budget < 1:
        raise SystemExit("BLOCKED: no available dial budget/in-flight capacity")
    if not eligible:
        raise SystemExit("BLOCKED: no founder-authorized, allow-listed, phone-verified voice candidates; worker remains disarmed")
    print("Preflight PASS: eligible voice candidates:", [
        {"id": lead.id, "company": lead.company} for lead in eligible
    ])
    print("Preflight PASS: dial budget:", budget)
finally:
    db.close()
PY

export AI_CALLING_ENABLED=1
export AUTO_AI_CALLING_ENABLED=1
pm2 restart purity-worker --update-env
sleep 5
WORKER_ID="$(pm2 jlist | node -e 'let s="";process.stdin.on("data",d=>s+=d);process.stdin.on("end",()=>{const p=JSON.parse(s).find(x=>x.name==="purity-worker");if(!p)process.exit(1);console.log(p.pm2_env.pm_id);});')"
ENV_OUT="$(pm2 env "$WORKER_ID")"
printf '%s\n' "$ENV_OUT" | grep -q '^AI_CALLING_ENABLED: 1$'
printf '%s\n' "$ENV_OUT" | grep -q '^AUTO_AI_CALLING_ENABLED: 1$'
printf '%s\n' "$ENV_OUT" | grep -Fq "OUTREACH_ALLOW_LIST: $OUTREACH_ALLOW_LIST"
echo "Verified: worker runtime flags and reviewed cohort are armed."
# Persist only after the live preflight and runtime verification both pass.
.venv/bin/python - <<'PY'
from pathlib import Path
import re
p = Path(".env")
s = p.read_text()
for key in ("AI_CALLING_ENABLED", "AUTO_AI_CALLING_ENABLED"):
    pattern = rf"(?m)^{key}=.*$"
    if re.search(pattern, s):
        s = re.sub(pattern, f"{key}=1", s, count=1)
    else:
        s += chr(10) + key + chr(61) + chr(49) + chr(10)
p.write_text(s)
PY
pm2 save
(crontab -l 2>/dev/null | grep -v "# ARM_B2B_CALLS_20261110" || true) | crontab -
echo "Successful one-time arm complete; recurring preflight cron removed."
