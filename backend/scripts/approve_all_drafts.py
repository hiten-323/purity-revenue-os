"""Approve all pending email drafts based on explicit founder go-ahead."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database.database import SessionLocal
from app.models.models import B2BLead, EmailDraft, WorkflowEvent
from app.services import founder_actions as fa
from app.services import send_queue as sq

def run_bulk_approval():
    db = SessionLocal()
    try:
        pending = (
            db.query(EmailDraft)
            .filter(EmailDraft.status.in_(["DRAFT", "PENDING", "EDITED"]))
            .all()
        )
        print(f"Found {len(pending)} pending drafts to process.")

        approved_count = 0
        skipped_count = 0

        for draft in pending:
            lead = db.query(B2BLead).filter(B2BLead.id == draft.lead_id).first()
            if not lead or not (lead.email or "").strip():
                draft.status = "BLOCKED_NO_EMAIL"
                skipped_count += 1
                continue

            # Mark draft as founder approved
            draft.status = "FOUNDER_APPROVED"
            draft.approved_at = datetime.utcnow()
            lead.email_approved_by_founder = True

            touch = draft.follow_up_type or "intro"
            note = "Founder explicit go-ahead for all drafts"
            subject = draft.subject or ""

            # 1. Operational queue signal for the worker drain
            sq.approve(lead, db, touch=touch, note=note, approved_by="FOUNDER")

            # 2. Human audit row
            fa.record(
                db,
                fa.APPROVE_OUTREACH,
                lead=lead,
                draft_id=draft.id,
                channel="email",
                decision="APPROVED",
                touch=touch,
                subject=subject,
                recipient=lead.email,
                company=lead.company,
                note=note,
                payload={"authorised": "one outbound message", "touch": touch},
                actor="FOUNDER",
            )
            approved_count += 1

        db.commit()
        print(f"Successfully approved {approved_count} drafts. ({skipped_count} skipped due to missing email).")
        return {"approved": approved_count, "skipped": skipped_count}
    finally:
        db.close()

if __name__ == "__main__":
    run_bulk_approval()
