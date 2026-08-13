"""
Founder-facing API: approve, reject, and inspect founder actions.

The founder's only required role in outreach is approval. Everything else
(draft generation, cadence, pacing, delivery) is handled by the worker.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.models import B2BLead
from app.services import founder_actions as fa

router = APIRouter(prefix="/founder", tags=["founder-actions"])


class ApproveBody(BaseModel):
    lead_id: int
    draft_id: int | None = None
    touch: str = ""
    note: str = ""
    subject: str = ""


class RejectBody(BaseModel):
    lead_id: int
    draft_id: int | None = None
    note: str = ""


class BulkApproveBody(BaseModel):
    """Approve several pending drafts in one founder action batch."""
    draft_ids: list[int] = Field(default_factory=list)
    note: str = ""


@router.get("/actions")
def get_founder_actions(
    limit: int = 50,
    action_type: str = "",
    db: Session = Depends(get_db),
):
    """Audit trail: every founder decision, newest first."""
    return {
        "count": None,
        "actions": fa.list_actions(db, limit=limit, action_type=action_type),
        "table": "founder_actions",
        "note": "Append-only. A revoke is a new row, not an edit.",
    }


@router.get("/pending")
def get_pending_approvals(limit: int = 40, db: Session = Depends(get_db)):
    """Drafts waiting on a founder yes/no. System already prepared these."""
    return fa.pending_for_founder(db, limit=limit)


@router.post("/approve")
def post_approve(body: ApproveBody, db: Session = Depends(get_db)):
    lead = db.query(B2BLead).filter(B2BLead.id == body.lead_id).first()
    if not lead:
        raise HTTPException(404, f"lead {body.lead_id} not found")
    return fa.approve_outreach(
        lead, db,
        draft_id=body.draft_id,
        touch=body.touch,
        note=body.note,
        subject=body.subject,
        actor="FOUNDER",
    )


@router.post("/reject")
def post_reject(body: RejectBody, db: Session = Depends(get_db)):
    lead = db.query(B2BLead).filter(B2BLead.id == body.lead_id).first()
    if not lead:
        raise HTTPException(404, f"lead {body.lead_id} not found")
    return fa.reject_outreach(
        lead, db,
        draft_id=body.draft_id,
        note=body.note,
        actor="FOUNDER",
    )


@router.post("/approve-bulk")
def post_approve_bulk(body: BulkApproveBody, db: Session = Depends(get_db)):
    """Approve many pending drafts at once. Each still gets its own founder_actions row."""
    from app.models.models import EmailDraft

    results = []
    for did in body.draft_ids:
        draft = db.query(EmailDraft).filter(EmailDraft.id == did).first()
        if not draft or draft.status != "DRAFT":
            results.append({"draft_id": did, "result": "skipped", "why": "not a pending draft"})
            continue
        lead = db.query(B2BLead).filter(B2BLead.id == draft.lead_id).first()
        if not lead:
            results.append({"draft_id": did, "result": "skipped", "why": "lead missing"})
            continue
        r = fa.approve_outreach(
            lead, db,
            draft_id=draft.id,
            touch=draft.follow_up_type or "",
            note=body.note,
            subject=draft.subject or "",
            actor="FOUNDER",
        )
        results.append({"draft_id": did, "result": "approved", **r})
    return {
        "approved": sum(1 for r in results if r.get("result") == "approved"),
        "results": results,
        "note": "worker drain will send these on the next cycle",
    }
