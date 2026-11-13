"""Fix 8: restore attempts for never-rang exhausted leads (AI_CALL_ATTEMPTED + FAILED + count=3)."""
from __future__ import annotations
import csv, json, re, sys
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from app.database.database import SessionLocal
from app.models.models import B2BLead, CallHistory
from app.services import founder_call_pipeline as pipeline
from app.services.call_intelligence.never_rang import retry_hours

BACKUP_DIR = Path(sys.argv[1])
DRY = "--dry-run" in sys.argv
BLOCK_TAILS = {"8591977190", "9855593323"}
DNC_NAME_RE = re.compile(r"salt.?n.?peppr|spot\s*on\s*hotel\s*kk\s*residency", re.I)
NOW = datetime.utcnow()

def phone_tail(p: str) -> str:
    return re.sub(r"\D", "", p or "")[-10:]

db = SessionLocal()
cohort = (
    db.query(B2BLead)
    .filter(
        B2BLead.ai_call_count >= 3,
        B2BLead.outreach_stage == pipeline.AI_CALL_ATTEMPTED,
        B2BLead.call_status == "FAILED",
    )
    .order_by(B2BLead.id)
    .all()
)
print(f"cohort_size={len(cohort)}")

# CSV backup
bak = BACKUP_DIR / "fix8_leads_before.csv"
with bak.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id","company","phone","outreach_stage","call_status","ai_call_count","ai_retry_after","do_not_call","dnc_reason","last_call_date"])
    for L in cohort:
        w.writerow([
            L.id, L.company, L.phone, L.outreach_stage, L.call_status,
            L.ai_call_count, L.ai_retry_after, L.do_not_call, L.dnc_reason, L.last_call_date,
        ])
print(f"backup_csv={bak} rows={len(cohort)}")

restored = 0
skipped_dnc = 0
skipped_block = 0
skipped_fsm = 0
errors = []
details = []

for L in cohort:
    name = str(L.company or "")
    tail = phone_tail(L.phone)
    if L.do_not_call or DNC_NAME_RE.search(name):
        skipped_dnc += 1
        details.append({"id": L.id, "action": "skip_dnc", "company": name[:40]})
        continue
    if tail in BLOCK_TAILS:
        skipped_block += 1
        details.append({"id": L.id, "action": "skip_block_phone", "tail": tail, "company": name[:40]})
        continue
    before_count = L.ai_call_count or 0
    before_stage = pipeline.stage_of(L)
    try:
        if not DRY:
            # Legal edge: AI_CALL_ATTEMPTED -> AI_NO_ANSWER (retry window)
            pipeline.advance(
                L, db, pipeline.AI_NO_ANSWER,
                note="Fix 8 2026-10-05: never-rang attempts restored (dispatch failed without ring)",
            )
            L.ai_call_count = 0
            L.call_status = "DISPATCH_FAILED"
            L.ai_retry_after = NOW + timedelta(hours=retry_hours())
            # Relabel attempt history rows that never became a real outcome
            rows = (
                db.query(CallHistory)
                .filter(
                    CallHistory.lead_id == L.id,
                    CallHistory.status == "AI_CALL_ATTEMPTED",
                )
                .all()
            )
            for row in rows:
                row.status = "DISPATCH_FAILED"
                if (row.call_status or "") in ("RECONCILED", "CALLING", "COMPLETED", ""):
                    row.call_status = "DISPATCH_FAILED"
                if not getattr(row, "summary", None):
                    row.summary = "Fix 8: never rang; attempt restored"
            restored += 1
        else:
            restored += 1
        details.append({
            "id": L.id, "action": "restored",
            "count_before": before_count, "stage_before": before_stage,
            "company": name[:40], "tail": tail,
        })
    except Exception as e:
        skipped_fsm += 1
        errors.append({"id": L.id, "error": str(e)[:200]})
        db.rollback()
        # new session continuity: expire and continue
        continue

if not DRY:
    db.commit()
else:
    db.rollback()

summary = {
    "dry_run": DRY,
    "cohort": len(cohort),
    "restored": restored,
    "skipped_dnc": skipped_dnc,
    "skipped_block": skipped_block,
    "skipped_fsm_or_error": skipped_fsm,
    "errors": errors[:20],
    "retry_hours": retry_hours(),
}
(BACKUP_DIR / "fix8_restore_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
# post counts
if not DRY:
    left = (
        db.query(B2BLead)
        .filter(
            B2BLead.ai_call_count >= 3,
            B2BLead.outreach_stage == pipeline.AI_CALL_ATTEMPTED,
            B2BLead.call_status == "FAILED",
        )
        .count()
    )
    restored_stage = (
        db.query(B2BLead)
        .filter(B2BLead.outreach_stage == pipeline.AI_NO_ANSWER, B2BLead.ai_call_count == 0, B2BLead.call_status == "DISPATCH_FAILED")
        .count()
    )
    print(f"remaining_exhausted_cohort={left}")
    print(f"now_AI_NO_ANSWER_count0_DISPATCH_FAILED={restored_stage}")
db.close()
