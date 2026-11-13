"""Release an AI-call attempt that never rang the business.

A dial that never reached the business's phone -- the LiveKit agent never
joined the room, the SIP trunk refused the INVITE before ringing, or the
telephony/speech provider is out of credit -- is not an attempt on the lead.
Counting it burns the lead's three-attempt budget on our own outage (on
2026-10-03, 1,179 leads were exhausted this way without ever ringing).

release_never_rang():
  * closes the call (lead.call_status = DISPATCH_FAILED, never CALLING and
    never NO_ANSWER), keeping the call_history row as an audit record with
    status DISPATCH_FAILED;
  * writes a CallResult with outcome DISPATCH_FAILED, connected=False,
    LOW_CONFIDENCE (excluded from learning rates);
  * gives the attempt back: when the dial had already been counted
    (stage AI_CALL_ATTEMPTED, ai_call_count incremented at dispatch) the
    count is decremented and the lead returns to the AI_NO_ANSWER retry
    window (the only legal FSM edge out of AI_CALL_ATTEMPTED that is not a
    decision), with ai_retry_after pushed out by NEVER_RANG_RETRY_HOURS;
  * never touches do_not_call, consent, or any terminal stage, and never
    dials.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

DISPATCH_FAILED = "DISPATCH_FAILED"
NEVER_RANG_RETRY_HOURS_ENV = "NEVER_RANG_RETRY_HOURS"
_DEFAULT_RETRY_HOURS = 20.0
# Same set the status endpoint treats as an open dial.
_OUTSTANDING = frozenset({"CALLING", "UNKNOWN_NO_RESULT"})
SIP_INVALID = "SIP_INVALID"
SIP_400_STRIKE_GAP = timedelta(hours=24)


_RANG_SIP = (180, 183, 486, 480, 487, 408, 600, 603)
_RANG_OUTCOMES = ("BUSY", "VOICEMAIL", "CALLEE_HUNGUP", "INCOMPLETE", "NOT_INTERESTED",
                  "CALLBACK_REQUESTED", "INTERESTED", "WRONG_PERSON", "SAMPLE_REQUESTED",
                  "CATALOGUE_REQUESTED", "WHATSAPP_OPT_IN")


def number_has_rung(db, lead, *, exclude_id=None) -> bool:
    """27 Oct audit (Hiten 26 Oct rule): a number that has ever rung or been
    answered is never quarantined as invalid, whatever a later SIP reject says
    (a 400/503 on a number that rang before is a carrier/route blip)."""
    try:
        from sqlalchemy import or_
        from app.services.call_intelligence.models import CallResult
        q = db.query(CallResult.id).filter(
            CallResult.lead_id == lead.id,
            or_(CallResult.connected.is_(True),
                CallResult.answered_at.isnot(None),
                CallResult.sip_status_code.in_(_RANG_SIP),
                CallResult.outcome.in_(_RANG_OUTCOMES)))
        if exclude_id is not None:
            q = q.filter(CallResult.id != exclude_id)
        return q.first() is not None
    except Exception:  # noqa: BLE001
        return False


def sip_reject_is_final(db, lead, code, *, now=None, exclude_id=None) -> bool:
    """27 Oct 20:27 IST (Hiten's rule, replaces the 26 Oct first-try 404/484
    quarantine): a carrier number-reject (SIP 400/404/484) is final only on the
    2nd strike - an EARLIER 400/404/484 on this lead at least 24h before now
    (two failed calls on different days) - and never if the number has ever
    rung or been answered."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    if code not in SIP_NUMBER_REJECTED:
        return False
    if number_has_rung(db, lead, exclude_id=exclude_id):
        return False
    now = now or datetime.utcnow()
    try:
        from app.services.call_intelligence.models import CallResult
        q = db.query(CallResult).filter(CallResult.lead_id == lead.id,
                                        CallResult.sip_status_code.in_(tuple(SIP_NUMBER_REJECTED)),
                                        CallResult.created_at <= now - SIP_400_STRIKE_GAP)
        if exclude_id is not None:
            q = q.filter(CallResult.id != exclude_id)
        return q.count() >= 1
    except Exception:  # noqa: BLE001
        return False
SIP_NUMBER_REJECTED = frozenset({400, 404, 484})


def retry_hours() -> float:
    try:
        v = float(os.getenv(NEVER_RANG_RETRY_HOURS_ENV, "") or _DEFAULT_RETRY_HOURS)
    except ValueError:
        v = _DEFAULT_RETRY_HOURS
    return max(1.0, v)


def _close_history(db, lead, detail: str | None) -> int:
    try:
        from app.models.models import CallHistory
    except Exception:  # noqa: BLE001
        return 0
    rows = (db.query(CallHistory)
              .filter(CallHistory.lead_id == lead.id, CallHistory.call_status == "CALLING",
                      CallHistory.status.in_(("AI_CALL_ATTEMPTED", "")))
              .order_by(CallHistory.id.desc()).all())
    for row in rows:
        row.call_status = DISPATCH_FAILED
        if (row.status or "") in ("AI_CALL_ATTEMPTED", ""):
            row.status = DISPATCH_FAILED
        if detail and not getattr(row, "summary", None):
            row.summary = detail[:1000]
    return len(rows)


def release_never_rang(db, lead, *, reason: str, meta: dict | None = None,
                       detail: str | None = None, now: datetime | None = None,
                       sip_status_code: int | None = None) -> dict:
    """Finalize a never-rang dial and give the attempt back. Idempotent per
    call: only an outstanding dial (call_status CALLING) is released."""
    from app.services import founder_call_pipeline as pipeline
    from app.services.call_intelligence.capture import finalize_call

    now = now or datetime.utcnow()
    reason = (reason or DISPATCH_FAILED).strip().upper()
    outstanding = (getattr(lead, "call_status", "") or "") in _OUTSTANDING
    released = False
    stage_before = pipeline.stage_of(lead)
    count_before = getattr(lead, "ai_call_count", 0) or 0
    if not outstanding:
        # A late report for a call that already concluded: nothing to release
        # and nothing to relabel (the earlier terminal state stands).
        return {"released": False, "outstanding": False, "stage_before": stage_before,
                "stage": stage_before, "ai_call_count_before": count_before,
                "ai_call_count": count_before, "termination_reason": reason}

    if stage_before == pipeline.AI_CALL_ATTEMPTED:
        # Counted at dispatch: give it back. AI_CALL_ATTEMPTED -> AI_NO_ANSWER
        # is a legal, non-decision edge; it is the retry window.
        try:
            pipeline.advance(lead, db, pipeline.AI_NO_ANSWER,
                             note=f"never rang ({reason}); attempt not counted")
            lead.ai_call_count = max(0, count_before - 1)
            released = True
        except ValueError as exc:
            log.warning("never-rang release refused for lead %s: %s", lead.id, exc)
    if pipeline.stage_of(lead) in (pipeline.AI_NO_ANSWER, pipeline.ELIGIBLE):
        lead.ai_retry_after = now + timedelta(hours=retry_hours())

    meta = dict(meta or {})
    # Relabel the open call_history row first so the audit trail says what
    # happened (dispatch failure, not a no-answer); finalize_call then finds
    # nothing left CALLING to relabel as ENDED.
    _close_history(db, lead, detail)
    finalize_call(db, lead, fsm_key=None, termination_reason=reason, source="engine",
                  meta=meta, summary=detail or f"never rang: {reason}",
                  sip_status_code=sip_status_code,
                  result_override={"outcome": DISPATCH_FAILED, "connected": False,
                                   "confidence": "LOW_CONFIDENCE",
                                   "termination_reason": reason},
                  now=now)
    lead.call_status = DISPATCH_FAILED
    # 13 Oct: carrier rejected the NUMBER itself (SIP 400/404/484) - the same
    # number will be rejected again, so stop re-dialing it every 20h. Call
    # queue only (email unaffected). Reversible: set phone_trust back to NULL.
    # 27 Oct 20:27 IST (Hiten): 400/404/484 all quarantine only on a 2nd
    # strike >= 24h after an earlier reject, never if the number ever rang.
    if (reason == "PRE_RING_FAILURE" and sip_status_code in SIP_NUMBER_REJECTED
            and (getattr(lead, "phone_trust", None) or "") not in ("INVALID", "SHARED_QUARANTINE")
            and sip_reject_is_final(db, lead, sip_status_code, now=now)):
        lead.phone_trust = SIP_INVALID
        log.warning("lead %s phone quarantined %s after SIP %s", lead.id, SIP_INVALID, sip_status_code)
    # 14 Oct: SIP 503 that repeats on the same number (2nd time) is number-specific
    # (leads 1025, 952 got 503 on two different days), not a carrier blip.
    elif (reason == "PRE_RING_FAILURE" and sip_status_code == 503
            and (getattr(lead, "phone_trust", None) or "") not in ("INVALID", "SHARED_QUARANTINE")):
        try:
            from app.services.call_intelligence.models import CallResult
            prior = (db.query(CallResult).filter(CallResult.lead_id == lead.id,
                                                CallResult.sip_status_code == 503).count())
        except Exception:  # noqa: BLE001
            prior = 0
        if prior >= 1 and not number_has_rung(db, lead):
            lead.phone_trust = SIP_INVALID
            log.warning("lead %s phone quarantined %s after repeated SIP 503", lead.id, SIP_INVALID)
    try:
        from app.observability import decision
        decision("ai_call.attempt", "RELEASED" if released else "NOT_COUNTED",
                 f"never rang ({reason}); attempts {count_before}->{lead.ai_call_count or 0}",
                 lead=lead)
    except Exception:  # noqa: BLE001
        pass
    return {"released": released, "outstanding": outstanding,
            "stage_before": stage_before, "stage": pipeline.stage_of(lead),
            "ai_call_count_before": count_before,
            "ai_call_count": getattr(lead, "ai_call_count", 0) or 0,
            "termination_reason": reason}
