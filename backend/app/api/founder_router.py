"""
Founder-facing API: approve, reject, and inspect founder actions.

The founder's only required role in outreach is approval. Everything else
(draft generation, cadence, pacing, delivery) is handled by the worker.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import require_api_admin
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


class AICallOutcomeBody(BaseModel):
    lead_id: int
    outcome: str
    summary: str = ""
    interest: str = ""
    callback_window: str = ""
    transcript: str = ""
    # Only meaningful with WHATSAPP_OPT_IN: a number the business read out on
    # the call. Empty means "the number you are calling" — see
    # founder_call_pipeline._call_whatsapp_destination.
    whatsapp_number: str = ""
    # What the call learned, for reporting only — founder_call_pipeline.
    # CALL_DETAIL_VALUES is the vocabulary; anything outside it is dropped.
    preferred_channel: str = ""
    handles_instant_coffee: str = ""
    decision_maker: str = ""
    objection: str = ""


@router.post("/ai-call-outcome", dependencies=[Depends(require_api_admin)])
def post_ai_call_outcome(body: AICallOutcomeBody, db: Session = Depends(get_db)):
    """Called by the Nuraveda voice agent (profiles/purity-coffee-b2b/agent.js)
    when a disclosed AI qualification call ends. Routes through
    founder_call_pipeline.record_ai_outcome — the sole authority for this
    state machine — rather than writing to the lead directly, for the same
    reason every other channel imports its gate instead of restating it.
    """
    from app.services import founder_call_pipeline as pipeline

    lead = db.query(B2BLead).filter(B2BLead.id == body.lead_id).first()
    if not lead:
        raise HTTPException(404, f"lead {body.lead_id} not found")

    key = (body.outcome or "").strip().upper()

    # Idempotency: a webhook/tool retry re-sending the SAME outcome for a
    # lead that has already recorded it must not be treated as an error.
    # record_ai_outcome()/advance() are deliberately strict FSM primitives —
    # a stage cannot legally transition to itself — so a bare retry raises
    # ValueError there. That strictness is correct for the state machine and
    # wrong for an HTTP boundary a network layer is allowed to retry; fixing
    # it here keeps advance() honest while making the webhook do what every
    # webhook must (Phase 8/15: "a webhook retry must not create duplicate
    # outcomes"). A DIFFERENT outcome for an already-attempted lead is not a
    # retry — MAX_AI_COLD_CALLS_PER_LEAD is 1, so there is no legitimate way
    # for a second real call to have happened, and that case still surfaces
    # as an error rather than being silently accepted.
    if lead.call_outcome_last == key and pipeline.stage_of(lead) != pipeline.ELIGIBLE:
        return {
            "status": "already_recorded",
            "lead_id": lead.id,
            "stage": pipeline.stage_of(lead),
            "note": "idempotent retry — this outcome was already recorded for this lead",
        }

    try:
        target_stage = pipeline.record_ai_outcome(
            lead, db, body.outcome,
            summary=body.summary,
            interest=body.interest,
            callback_window=body.callback_window,
            transcript=body.transcript,
            whatsapp_number=body.whatsapp_number,
            details={"preferred_channel": body.preferred_channel,
                     "handles_instant_coffee": body.handles_instant_coffee,
                     "decision_maker": body.decision_maker,
                     "objection": body.objection},
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))

    # record_ai_outcome()/advance() never commit themselves — every other
    # caller in this codebase (calling_agent.py's _place_qualification_call,
    # etc.) commits explicitly after calling into the
    # pipeline, and this endpoint is the one place that didn't. Without this,
    # get_db()'s `finally: db.close()` silently discards every mutation
    # record_ai_outcome made — the response still reports the correct
    # computed stage (it's read from the in-memory object before the
    # session closes), so the bug is invisible unless something re-queries
    # with a fresh session. Found exactly that way, via a live controlled
    # call whose outcome never appeared in the database afterward.
    db.commit()

    return {"status": "recorded", "lead_id": lead.id, "stage": target_stage}
