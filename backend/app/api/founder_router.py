"""
Founder-facing API: approve, reject, and inspect founder actions.

The founder's only required role in outreach is approval. Everything else
(draft generation, cadence, pacing, delivery) is handled by the worker.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

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
    # Call-level facts for the structured CallResult (call_intelligence).
    # All optional: an older agent that sends none of them still works.
    call_ref: str = ""
    room_name: str = ""
    sip_call_id: str = ""
    provider_call_id: str = ""
    started_at: str = ""
    answered_at: str = ""
    ended_at: str = ""
    duration_seconds: Optional[float] = None
    turns: Optional[list] = None
    language: str = ""
    opener_variant: str = ""
    script_variant: str = ""
    termination_reason: str = ""


# call_status values meaning "a dial went out and no real result is in yet".
# UNKNOWN_NO_RESULT is what the stale-call reconciler writes; a late but real
# report (the sidecar retries a no-answer for up to ~3h) must still land.
_DIAL_OUTSTANDING = {"CALLING", "UNKNOWN_NO_RESULT"}


def _call_meta(body) -> dict:
    keys = ("call_ref", "room_name", "sip_call_id", "provider_call_id", "started_at",
            "answered_at", "ended_at", "duration_seconds", "turns", "language",
            "opener_variant", "script_variant", "termination_reason")
    return {k: getattr(body, k) for k in keys
            if getattr(body, k, None) not in (None, "", [])}


def _already_final(db, call_ref: str) -> bool:
    if not call_ref:
        return False
    try:
        from app.services.call_intelligence.capture import find_result
        row = find_result(db, call_ref)
    except Exception:  # noqa: BLE001
        return False
    return row is not None and row.status == "FINAL" and row.source in ("voice_agent", "founder")


@router.get("/ai-call-learning", dependencies=[Depends(require_api_admin)])
def get_ai_call_learning(segment: str = "", days: int = 30, limit: int = 500,
                         db: Session = Depends(get_db)):
    """Return advisory call-learning aggregates for the voice agent.

    This endpoint exposes patterns only. It never grants calling permission,
    changes DND/consent, schedules a callback, or returns raw transcripts.
    """
    from app.services.call_learning import build_strategy_hints

    return build_strategy_hints(
        db,
        segment=segment.strip() or None,
        days=min(max(days, 1), 90),
        limit=min(max(limit, 1), 1000),
    )


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
    #
    # Call learning loop fix: with retries (AI_NO_ANSWER -> redial) a second
    # REAL attempt can legitimately report the same outcome as the first, and
    # the old check swallowed it -- the lead then sat in CALLING forever.
    # A report is now a duplicate when (a) its call_ref already has a final
    # result, or (b) with no call_ref, the outcome matches AND no dial is
    # outstanding (call_status != CALLING: the first report cleared it).
    meta = _call_meta(body)
    duplicate = _already_final(db, body.call_ref) if body.call_ref else (
        lead.call_outcome_last == key
        and pipeline.stage_of(lead) != pipeline.ELIGIBLE
        and (lead.call_status or "") not in _DIAL_OUTSTANDING)
    if duplicate:
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
            call_meta=meta,
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

    return {"status": "recorded", "lead_id": lead.id, "stage": target_stage,
            "call_status": lead.call_status}


class AICallStatusBody(BaseModel):
    """An engine-side termination (no answer, busy, SIP error, agent crash,
    timeout) -- the paths that never reach the model's outcome tool. Sent by
    the sidecar scheduler's onNoAnswer hook and the agent's SIP-failure path.
    """
    lead_id: int
    reason: str = ""            # NO_ANSWER | BUSY | VOICEMAIL | FAILED | SIP_ERROR | ...
    sip_status_code: Optional[int] = None
    call_ref: str = ""
    room_name: str = ""
    sip_call_id: str = ""
    provider_call_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    attempts: Optional[int] = None
    detail: str = ""


@router.post("/ai-call-status", dependencies=[Depends(require_api_admin)])
def post_ai_call_status(body: AICallStatusBody, db: Session = Depends(get_db)):
    """Terminal writeback for calls that ended without a model outcome.

    Maps the engine reason (or SIP code) onto the existing FSM outcome
    vocabulary and routes through record_ai_outcome. If no dial is
    outstanding or the FSM refuses, the call is still finalised
    (call_status leaves CALLING, a CallResult is written) without touching
    outreach_stage. Never dials, never sends.
    """
    from app.services import founder_call_pipeline as pipeline
    from app.services.call_intelligence.capture import finalize_call
    from app.services.call_intelligence.taxonomy import (
        ENGINE_TERMINATIONS, NEVER_RANG_TERMINATIONS, termination_from_sip)

    lead = db.query(B2BLead).filter(B2BLead.id == body.lead_id).first()
    if not lead:
        raise HTTPException(404, f"lead {body.lead_id} not found")
    reason = ((body.reason or "").strip().upper()
              or termination_from_sip(body.sip_status_code) or "")
    if reason == "SIP_ERROR" and body.sip_status_code is not None:
        reason = termination_from_sip(body.sip_status_code) or reason
    if reason not in ENGINE_TERMINATIONS:
        raise HTTPException(400, f"unknown termination reason {body.reason!r}; expected one of "
                                 + ", ".join(sorted(ENGINE_TERMINATIONS)))
    if body.call_ref and _already_final(db, body.call_ref):
        return {"status": "already_recorded", "lead_id": lead.id,
                "stage": pipeline.stage_of(lead), "call_status": lead.call_status}
    fsm_key = ENGINE_TERMINATIONS[reason]
    if reason in NEVER_RANG_TERMINATIONS:
        # The business's phone never rang: not an attempt, not a no-answer.
        from app.services.call_intelligence.never_rang import release_never_rang
        meta = {k: v for k, v in {
            "call_ref": body.call_ref, "room_name": body.room_name,
            "sip_call_id": body.sip_call_id, "provider_call_id": body.provider_call_id,
            "started_at": body.started_at, "ended_at": body.ended_at,
        }.items() if v not in (None, "")}
        res = release_never_rang(db, lead, reason=reason, meta=meta,
                                 detail=(body.detail or "")[:500] or None)
        db.commit()
        return {"status": "released" if res["released"] else "recorded",
                "lead_id": lead.id, "fsm_applied": False,
                "stage": pipeline.stage_of(lead), "call_status": lead.call_status,
                "termination_reason": reason, "attempt_released": res["released"],
                "ai_call_count": res["ai_call_count"]}
    meta = {k: v for k, v in {
        "call_ref": body.call_ref, "room_name": body.room_name,
        "sip_call_id": body.sip_call_id, "provider_call_id": body.provider_call_id,
        "started_at": body.started_at, "ended_at": body.ended_at,
    }.items() if v not in (None, "")}
    # Only an outstanding dial gets a new FSM outcome; a late engine report
    # for an already-concluded call must not count another attempt.
    fsm_applied = False
    if (lead.call_status or "") in _DIAL_OUTSTANDING:
        try:
            pipeline.record_ai_outcome(
                lead, db, fsm_key, summary=(body.detail or "")[:500],
                call_meta={**meta, "termination_reason": reason, "source": "engine",
                           "sip_status_code": body.sip_status_code})
            fsm_applied = True
        except ValueError:
            db.rollback()
            lead = db.query(B2BLead).filter(B2BLead.id == body.lead_id).first()
    if not fsm_applied:
        finalize_call(db, lead, fsm_key=fsm_key, termination_reason=reason, source="engine",
                      meta=meta, summary=body.detail or None,
                      sip_status_code=body.sip_status_code)
    db.commit()
    return {"status": "recorded", "lead_id": lead.id, "fsm_applied": fsm_applied,
            "stage": pipeline.stage_of(lead), "call_status": lead.call_status,
            "termination_reason": reason}


class SystemAlertBody(BaseModel):
    """An operational alert for the founder (provider credit exhausted, voice
    agent wedged, auto-dispatch paused). Emailed to the founder only."""
    kind: str = Field(..., min_length=2, max_length=60)
    detail: str = Field("", max_length=4000)
    key: str = Field("", max_length=120)


@router.post("/system-alert", dependencies=[Depends(require_api_admin)])
def post_system_alert(body: SystemAlertBody):
    """Email the founder a rate-limited system alert. Never contacts a lead."""
    from app.services.founder_alert import send_system_alert

    return send_system_alert(body.kind, body.detail, key=body.key or None)


# ── the founder call queue ───────────────────────────────────────────────────
# Where an AI call that ended in "please call me back" / "I'd like to speak to
# someone" becomes work a person can see and close. Admin-only: every item
# carries a phone number and the call summary.

class FounderCallDone(BaseModel):
    outcome: str
    note: str = ""


@router.get("/call-queue", dependencies=[Depends(require_api_admin)])
def get_founder_call_queue(limit: int = 50, db: Session = Depends(get_db)):
    """Open founder-call work items, oldest ask first."""
    from app.services import founder_call_pipeline as pipeline

    items = pipeline.founder_call_queue(db, limit=limit)
    return {
        "count": len(items),
        "items": [{
            "lead_id": i.lead_id,
            "requested_at": i.requested_at.isoformat() if i.requested_at else None,
            "requested_by": i.requested_by,
            "reason": (i.payload or {}).get("reason"),
            "note": (i.payload or {}).get("note"),
            "brief": (i.payload or {}).get("founder_brief"),
        } for i in items],
    }


@router.post("/call-queue/{lead_id}/complete", dependencies=[Depends(require_api_admin)])
def post_founder_call_complete(lead_id: int, body: FounderCallDone,
                               db: Session = Depends(get_db)):
    """The founder made the call: close the item and record what happened."""
    from app.services import founder_call_pipeline as pipeline

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if lead is None:
        raise HTTPException(404, f"lead {lead_id} not found")
    try:
        stage = pipeline.complete_founder_call(lead, db, outcome=body.outcome, note=body.note)
    except ValueError as e:
        db.rollback()
        raise HTTPException(409, str(e))
    db.commit()
    return {"status": "completed", "lead_id": lead.id, "stage": stage}
