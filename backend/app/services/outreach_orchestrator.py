r"""
Smart Outreach coordinates email, WhatsApp and phone. Phone is the consent
acquisition step for WhatsApp: a verified WhatsApp number proves technical
reachability, never permission. WhatsApp is proposed only after the AI call
records explicit WhatsApp consent for that same verified number.

The programme is sequential, not simultaneous. The first automated touch is
one disclosed AI qualification call. Once the call records WHATSAPP_OPT_IN,
WhatsApp may be proposed. Email remains an independent channel and is still
subject to its own trust/approval gate.

This module PROPOSES. It does not send. Channel-specific execution remains in
the founder approval queue and the channel sender chokepoints.
"""
from __future__ import annotations

from datetime import datetime

EMAIL = "email"
WHATSAPP = "whatsapp"
PHONE = "phone"
LINKEDIN = "linkedin"
CHANNELS = (EMAIL, WHATSAPP, PHONE, LINKEDIN)

SEQUENCE = (
    (0, PHONE, "ai_whatsapp_consent"),
    (2, WHATSAPP, "consent_follow_up"),
    (4, EMAIL, "introduction"),
    (6, EMAIL, "different_angle"),
    (8, WHATSAPP, "follow_up"),
    (12, EMAIL, "polite_close"),
)

ENGAGED_OUTCOMES = (
    "INTERESTED", "NOT_INTERESTED", "MEETING_REQUESTED", "SAMPLE_REQUESTED",
    "PRICE_OBJECTION", "EXISTING_SUPPLIER", "SEND_DETAILS", "SEND_PRICING",
    "SEND_WHATSAPP", "DO_NOT_CONTACT", "WRONG_PERSON",
)
SUPPRESSING_OUTCOMES = ("DO_NOT_CONTACT", "NOT_INTERESTED")


def _email_ok(lead, db):
    from app.services.trust_promoter import may_send
    return may_send(lead)


def _whatsapp_ok(lead, db):
    """Require verified WhatsApp reachability AND AI-call consent."""
    from app.services.whatsapp_sender import consent_check

    if not (getattr(lead, "whatsapp_number", "") or "").strip():
        return False, "no WhatsApp-reachable number on record"

    verified = getattr(lead, "whatsapp_verified", None)
    if verified is None:
        return False, "WhatsApp account never verified for this number"
    if not verified:
        return False, "this number has no WhatsApp account (checked)"

    if (getattr(lead, "consent_source", "") or "").upper() != "AI_CALL_WHATSAPP_REQUEST":
        return False, "WhatsApp opt-in has not been obtained by the AI consent call"

    if not (getattr(lead, "ai_call_count", 0) or 0) > 0:
        return False, "WhatsApp consent call has not been completed"

    return consent_check(lead)


def _phone_ok(lead, db):
    from app.services.founder_call_pipeline import may_place_ai_call
    return may_place_ai_call(lead)


def _linkedin_ok(lead, db):
    return False, ("no adapter, and automated LinkedIn outreach violates their "
                   "user agreement — treat as a manual channel")


GATES = {EMAIL: _email_ok, WHATSAPP: _whatsapp_ok, PHONE: _phone_ok, LINKEDIN: _linkedin_ok}


def eligibility(lead, db) -> dict:
    from app.observability import ALLOWED, REFUSED, decision
    out = {}
    for ch in CHANNELS:
        try:
            ok, why = GATES[ch](lead, db)
        except Exception as exc:
            ok, why = False, (f"gate unavailable ({exc.__class__.__name__}); refusing — "
                              "a channel whose rule cannot be reached has not satisfied it")
        decision(f"{ch}.eligibility", ALLOWED if ok else REFUSED, why,
                 lead=lead, company=getattr(lead, "company", ""))
        out[ch] = {"eligible": bool(ok), "reason": why}
    return out


def reachable_channels(lead, db) -> list:
    return [c for c, v in eligibility(lead, db).items() if v["eligible"]]


def stop_reason(lead, db) -> str:
    if getattr(lead, "do_not_call", False):
        return "do_not_call is set"
    status = (getattr(lead, "consent_status", "") or "").upper()
    if status in ("OPT_OUT", "REFUSED", "UNSUBSCRIBED"):
        return f"consent_status={status} — permanently suppressed"

    from app.models.models import LeadInteraction
    engaged = (db.query(LeadInteraction)
                 .filter(LeadInteraction.lead_id == lead.id,
                         LeadInteraction.outcome.in_(ENGAGED_OUTCOMES))
                 .order_by(LeadInteraction.occurred_at.desc())
                 .first())
    if engaged:
        if engaged.outcome in SUPPRESSING_OUTCOMES:
            return f"the business said {engaged.outcome} — suppressed"
        return (f"the business engaged ({engaged.outcome}) — automated sequence "
                f"stops, a human takes it from here")

    stage = (getattr(lead, "outreach_stage", "") or "").upper()
    if stage in ("AI_OPTED_OUT", "AI_NOT_INTERESTED", "CLOSED_NO_FIT"):
        return f"pipeline stage {stage} is terminal"
    return ""


def _touches_done(lead, db) -> set:
    from app.models.models import WorkflowEvent
    done = set()
    evs = (db.query(WorkflowEvent)
             .filter(WorkflowEvent.lead_id == lead.id,
                     WorkflowEvent.event_type.in_(("EMAIL_SENT", "WHATSAPP_SENT", "FOUNDER_CALL_PIPELINE")))
             .all())
    for e in evs:
        ch = {"EMAIL_SENT": EMAIL, "WHATSAPP_SENT": WHATSAPP}.get(e.event_type)
        if ch:
            done.add((ch, e.occurred_at))
    if (getattr(lead, "ai_call_count", 0) or 0) > 0:
        done.add((PHONE, getattr(lead, "last_call_date", None)))
    return done


def next_touch(lead, db, *, started: datetime = None) -> dict:
    from app.observability import REFUSED, STOP, WAIT, decision

    stop = stop_reason(lead, db)
    if stop:
        decision("orchestrator.next_touch", STOP, stop, lead=lead)
        return {"action": "STOP", "reason": stop, "channel": None}

    elig = eligibility(lead, db)
    if not any(v["eligible"] for v in elig.values()):
        decision("orchestrator.next_touch", REFUSED,
                 "no channel can reach this business today", lead=lead,
                 blocked=",".join(sorted(elig)))
        from app.services import lead_journal as journal
        journal.record(lead, db, method=journal.ORCHESTRATOR, outcome=journal.NO_CHANNEL,
                       remark="; ".join(f"{c}: {elig[c]['reason']}" for c in CHANNELS),
                       by="orchestrator")
        return {"action": "UNREACHABLE", "channel": None,
                "reason": "no channel can lawfully or technically reach this business today",
                "blocked_by": {c: v["reason"] for c, v in elig.items()}}

    started = started or getattr(lead, "stage_entered_date", None) or datetime.utcnow()
    age_days = max(0, (datetime.utcnow() - started).days)
    done = {c for c, _ in _touches_done(lead, db)}

    from app.observability import DONE, SKIPPED
    for day, channel, angle in SEQUENCE:
        if channel in done:
            decision(f"{channel}.sequence", SKIPPED, "already attempted on this lead",
                     lead=lead, day=day)
            continue
        if not elig[channel]["eligible"]:
            decision(f"{channel}.sequence", SKIPPED, elig[channel]["reason"],
                     lead=lead, day=day)
            continue
        if age_days < day:
            decision("orchestrator.next_touch", WAIT,
                     f"next touch is {channel} on day {day}", lead=lead,
                     due_in_days=day - age_days, age_days=age_days)
            return {"action": "WAIT", "channel": channel, "angle": angle,
                    "due_in_days": day - age_days,
                    "reason": f"next touch is {channel} on day {day}"}
        decision("orchestrator.next_touch", "PROPOSE",
                 f"{channel}/{angle} is due (day {day}, lead is {age_days}d old) — awaiting founder approval",
                 lead=lead)
        from app.services import lead_journal as journal
        journal.record(lead, db, method=channel, outcome=journal.QUEUED,
                       remark=(f"day {day} of the sequence, angle '{angle}'. Eligible: "
                               f"{elig[channel]['reason']}. Awaiting founder approval — nothing has been sent."),
                       by="orchestrator")
        return {"action": "PROPOSE", "channel": channel, "angle": angle, "day": day,
                "reason": elig[channel]["reason"],
                "note": "requires founder approval before it is sent"}

    decision("orchestrator.next_touch", DONE,
             "every eligible channel in the sequence has been attempted", lead=lead)
    return {"action": "COMPLETE", "channel": None,
            "reason": "every eligible channel in the sequence has been attempted"}


def reachability_report(db, limit: int = 0) -> dict:
    from app.models.models import B2BLead
    from collections import Counter
    leads = db.query(B2BLead).limit(limit).all() if limit else db.query(B2BLead).all()
    counts = Counter()
    blockers = {c: Counter() for c in CHANNELS}
    reach_any = 0
    stopped = 0
    for l in leads:
        if stop_reason(l, db):
            stopped += 1
            continue
        elig = eligibility(l, db)
        any_ok = False
        for ch, v in elig.items():
            if v["eligible"]:
                counts[ch] += 1
                any_ok = True
            else:
                blockers[ch][str(v["reason"])[:70]] += 1
        reach_any += 1 if any_ok else 0
    return {"leads": len(leads), "suppressed_or_replied": stopped,
            "reachable_on_at_least_one_channel": reach_any,
            "by_channel": dict(counts),
            "top_blockers": {c: blockers[c].most_common(3) for c in CHANNELS}}