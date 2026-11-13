"""Fix 8 batched restore — commits every BATCH leads to avoid sqlite lock."""
from __future__ import annotations
import csv, json, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from app.database.database import SessionLocal
from app.models.models import B2BLead, CallHistory
from app.services import founder_call_pipeline as pipeline
from app.services.call_intelligence.never_rang import retry_hours

BACKUP_DIR = Path(sys.argv[1])
BATCH = 40
BLOCK_TAILS = {"8591977190", "9855593323"}
DNC_NAME_RE = re.compile(r"salt.?n.?peppr|spot\s*on\s*hotel\s*kk\s*residency", re.I)
NOW = datetime.utcnow()

def phone_tail(p: str) -> str:
    return re.sub(r"\D", "", p or "")[-10:]

db = SessionLocal()
# bump sqlite busy timeout if possible
try:
    db.execute(__import__("sqlalchemy").text("PRAGMA busy_timeout=60000"))
except Exception:
    pass

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
print(f"cohort_remaining={len(cohort)}")

restored = skipped_dnc = skipped_block = errors_n = 0
error_samples = []
batch = []

def flush(batch_leads):
    global restored, errors_n
    if not batch_leads:
        return
    try:
        db.commit()
        restored += len(batch_leads)
        print(f"committed_batch size={len(batch_leads)} total_restored={restored}")
    except Exception as e:
        db.rollback()
        errors_n += len(batch_leads)
        error_samples.append(str(e)[:300])
        print(f"batch_failed size={len(batch_leads)} err={e}")
        # retry one-by-one
        for L in batch_leads:
            try:
                # re-fetch
                lead = db.get(B2BLead, L.id)
                if not lead or lead.ai_call_count < 3:
                    continue
                if lead.outreach_stage != pipeline.AI_CALL_ATTEMPTED or lead.call_status != "FAILED":
                    continue
                pipeline.advance(lead, db, pipeline.AI_NO_ANSWER, note="Fix 8 2026-10-05: never-rang attempts restored (dispatch failed without ring)")
                lead.ai_call_count = 0
                lead.call_status = "DISPATCH_FAILED"
                lead.ai_retry_after = NOW + timedelta(hours=retry_hours())
                for row in db.query(CallHistory).filter(CallHistory.lead_id == lead.id, CallHistory.status == "AI_CALL_ATTEMPTED").all():
                    row.status = "DISPATCH_FAILED"
                    if (row.call_status or "") in ("RECONCILED", "CALLING", "COMPLETED", ""):
                        row.call_status = "DISPATCH_FAILED"
                db.commit()
                restored += 1
                errors_n -= 1
            except Exception as e2:
                db.rollback()
                error_samples.append(f"id={getattr(L,'id',None)} {e2}"[:200])
                time.sleep(0.05)

for L in cohort:
    name = str(L.company or "")
    tail = phone_tail(L.phone)
    if L.do_not_call or DNC_NAME_RE.search(name):
        skipped_dnc += 1
        continue
    if tail in BLOCK_TAILS:
        skipped_block += 1
        continue
    try:
        pipeline.advance(L, db, pipeline.AI_NO_ANSWER, note="Fix 8 2026-10-05: never-rang attempts restored (dispatch failed without ring)")
        L.ai_call_count = 0
        L.call_status = "DISPATCH_FAILED"
        L.ai_retry_after = NOW + timedelta(hours=retry_hours())
        for row in db.query(CallHistory).filter(CallHistory.lead_id == L.id, CallHistory.status == "AI_CALL_ATTEMPTED").all():
            row.status = "DISPATCH_FAILED"
            if (row.call_status or "") in ("RECONCILED", "CALLING", "COMPLETED", ""):
                row.call_status = "DISPATCH_FAILED"
        batch.append(L)
        if len(batch) >= BATCH:
            flush(batch)
            batch = []
            time.sleep(0.1)
    except Exception as e:
        db.rollback()
        errors_n += 1
        error_samples.append(f"id={L.id} {e}"[:200])
        batch = []

flush(batch)

left = db.query(B2BLead).filter(B2BLead.ai_call_count >= 3, B2BLead.outreach_stage == pipeline.AI_CALL_ATTEMPTED, B2BLead.call_status == "FAILED").count()
shape = db.query(B2BLead).filter(B2BLead.outreach_stage == pipeline.AI_NO_ANSWER, B2BLead.ai_call_count == 0, B2BLead.call_status == "DISPATCH_FAILED").count()
summary = {
    "restored": restored,
    "skipped_dnc": skipped_dnc,
    "skipped_block": skipped_block,
    "errors": errors_n,
    "error_samples": error_samples[:10],
    "remaining_exhausted_cohort": left,
    "AI_NO_ANSWER_count0_DISPATCH_FAILED": shape,
}
(BACKUP_DIR / "fix8_restore_summary_live.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
db.close()
