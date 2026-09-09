r"""
Which channel can actually reach this business, and what should happen next.

The policy
----------
Every qualified prospect enters a multi-channel programme. Channels are
attempted in sequence -- not all at once, not blindly -- until a reply, a
suppression, or the end of the programme. A channel is skipped when it cannot
lawfully or technically reach that particular business.

    Day 0   email       introduction
    Day 2   whatsapp    short, contextual
    Day 4   phone       one disclosed AI qualification call
    Day 6   email       different angle
    Day 8   whatsapp    follow-up
    Day 12  email       close the loop politely

LinkedIn is deliberately absent from the sequence. There is no adapter, and
automating connection requests violates LinkedIn's user agreement; a channel
that cannot be run without breaking terms is not a channel this system offers.
It is reported as ineligible with that reason rather than silently dropped.

This module PROPOSES. It does not send.
--------------------------------------
next_touch() returns what should happen. Executing it stays with the founder
approval queue and each channel's own sender, which is where suppression,
frequency caps, throttling and send-proof live. An orchestrator that dispatched
directly would be a seventh path around the chokepoint -- the exact defect
tender_auto_pricer had.

Every gate is IMPORTED, never restated
--------------------------------------
    email     trust_promoter.may_send          (trust AND confidence >= 40)
    whatsapp  whatsapp_sender.consent_check    (Meta opt-in vocabulary)
    phone     founder_call_pipeline.may_place_ai_call  (one call, DND scrub)

This codebase has produced the same bug seven times: a second copy of a rule
that drifts from the first, and the drift shows up as contact nobody agreed to.
A module whose whole purpose is to coordinate channels is the most tempting
place in the system to restate their rules. So it holds none of its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta

EMAIL = "email"
WHATSAPP = "whatsapp"
PHONE = "phone"
LINKEDIN = "linkedin"
CHANNELS = (EMAIL, WHATSAPP, PHONE, LINKEDIN)

# (day offset, channel, angle) -- the angle names WHY this touch differs, so a
# second email is a different argument rather than the same pitch resent.
SEQUENCE = (
    (0, EMAIL, "introduction"),
    (2, WHATSAPP, "short_contextual"),
    (4, PHONE, "ai_qualification"),
    (6, EMAIL, "different_angle"),
    (8, WHATSAPP, "follow_up"),
    (12, EMAIL, "polite_close"),
)

# Engagement stops the sequence. These are LeadInteraction.outcome values that
# mean the BUSINESS actually spoke to us -- taken from the values the table
# really holds, not from a vocabulary invented here.
#
# BUSY, NO_ANSWER, CALL_LATER and GATEKEEPER are deliberately absent: nobody
# reached the buyer, so the sequence should continue. Treating "the receptionist
# answered" as engagement would silence outreach to every business with a front
# desk.
ENGAGED_OUTCOMES = (
    "INTERESTED", "NOT_INTERESTED", "MEETING_REQUESTED", "SAMPLE_REQUESTED",
    "PRICE_OBJECTION", "EXISTING_SUPPLIER", "SEND_DETAILS", "SEND_PRICING",
    "SEND_WHATSAPP", "DO_NOT_CONTACT", "WRONG_PERSON",
)

# Of those, the ones that must stop outreach permanently rather than hand over
# to a human.
SUPPRESSING_OUTCOMES = ("DO_NOT_CONTACT", "NOT_INTERESTED")


# ------------------------------------------------------------ eligibility --

def _email_ok(lead, db):
    from app.services.trust_promoter import may_send
    return may_send(lead)


def _whatsapp_ok(lead, db):
    from app.services.whatsapp_sender import consent_check
    if not (getattr(lead, "whatsapp_number", "") or "").strip():
        return False, "no WhatsApp-reachable number on record"
    return consent_check(lead)


def _phone_ok(lead, db):
    from app.services.founder_call_pipeline import may_place_ai_call
    return may_place_ai_call(lead)


def _linkedin_ok(lead, db):
    # Not "not built yet". Automating connection requests and messages breaks
    # LinkedIn's user agreement, and a channel that cannot be run without
    # breaking terms is not one this system will offer. Kept in CHANNELS so the
    # report says so out loud instead of leaving a silent gap.
    return False, ("no adapter, and automated LinkedIn outreach violates their "
                   "user agreement — treat as a manual channel")


GATES = {
    EMAIL: _email_ok,
    WHATSAPP: _whatsapp_ok,
    PHONE: _phone_ok,
    LINKEDIN: _linkedin_ok,
}


def eligibility(lead, db) -> dict:
    """Per channel: can we reach this business, and if not, why not.

    Reasons are the product here. "0 reachable" is not useful; "0 reachable
    because nobody has opted in to WhatsApp" tells you what to go and fix.
    """
    from app.observability import ALLOWED, REFUSED, decision

    out = {}
    for ch in CHANNELS:
        try:
            ok, why = GATES[ch](lead, db)
        except Exception as exc:  # noqa: BLE001 - an unreachable gate is a NO
            ok, why = False, (f"gate unavailable ({exc.__class__.__name__}); "
                              f"refusing — a channel whose rule cannot be "
                              f"reached has not satisfied it")
        # The reason logged is the one the channel's own gate returned. It is
        # not paraphrased here: a log that reads better than the code produced
        # is worse than none, because it will be believed.
        decision(f"{ch}.eligibility", ALLOWED if ok else REFUSED, why,
                 lead=lead, company=getattr(lead, "company", ""))
        out[ch] = {"eligible": bool(ok), "reason": why}
    return out


def reachable_channels(lead, db) -> list:
    return [c for c, v in eligibility(lead, db).items() if v["eligible"]]


# ------------------------------------------------------------- hard stops --

def stop_reason(lead, db) -> str:
    """Why automated outreach must not continue. Empty string means continue.

    Checked before any touch is proposed, because the cheapest wrong touch is
    the one sent to somebody who already answered.
    """
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


# --------------------------------------------------------------- planning --

def _touches_done(lead, db) -> set:
    """(day, channel) pairs already attempted, from the system's own records."""
    from app.models.models import WorkflowEvent
    done = set()
    evs = (db.query(WorkflowEvent)
             .filter(WorkflowEvent.lead_id == lead.id,
                     WorkflowEvent.event_type.in_(
                         ("EMAIL_SENT", "WHATSAPP_SENT", "FOUNDER_CALL_PIPELINE")))
             .all())
    for e in evs:
        ch = {"EMAIL_SENT": EMAIL, "WHATSAPP_SENT": WHATSAPP}.get(e.event_type)
        if ch:
            done.add((ch, e.occurred_at))
    if (getattr(lead, "ai_call_count", 0) or 0) > 0:
        done.add((PHONE, getattr(lead, "last_call_date", None)))
    return done


def next_touch(lead, db, *, started: datetime = None) -> dict:
    """The single next action, or an explanation of why there is none.

    Never returns a touch on an ineligible channel and never returns one after
    a stop. Executing what comes back is somebody else's job.
    """
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
        # On the business's own record, with each channel's own reason. This
        # is the one an operator reads when asking "why has nothing happened
        # to this account?". Deduped, so a nightly sweep that reaches the same
        # conclusion does not write it again.
        from app.services import lead_journal as journal
        journal.record(
            lead, db, method=journal.ORCHESTRATOR, outcome=journal.NO_CHANNEL,
            remark="; ".join(f"{c}: {elig[c]['reason']}" for c in CHANNELS),
            by="orchestrator")
        return {"action": "UNREACHABLE", "channel": None,
                "reason": "no channel can lawfully or technically reach this "
                          "business today",
                "blocked_by": {c: v["reason"] for c, v in elig.items()}}

    started = started or getattr(lead, "stage_entered_date", None) or datetime.utcnow()
    age_days = max(0, (datetime.utcnow() - started).days)
    done = {c for c, _ in _touches_done(lead, db)}

    from app.observability import DONE, SKIPPED

    for day, channel, angle in SEQUENCE:
        if channel in done:
            decision(f"{channel}.sequence", SKIPPED, "already attempted on this lead",
                     lead=lead, day=day)
            continue                      # that channel has had its turn
        if not elig[channel]["eligible"]:
            decision(f"{channel}.sequence", SKIPPED, elig[channel]["reason"],
                     lead=lead, day=day)
            continue                      # skip, do not stall the sequence
        if age_days < day:
            decision("orchestrator.next_touch", WAIT,
                     f"next touch is {channel} on day {day}", lead=lead,
                     due_in_days=day - age_days, age_days=age_days)
            return {"action": "WAIT", "channel": channel, "angle": angle,
                    "due_in_days": day - age_days,
                    "reason": f"next touch is {channel} on day {day}"}
        decision("orchestrator.next_touch", "PROPOSE",
                 f"{channel}/{angle} is due (day {day}, lead is {age_days}d old) "
                 f"— awaiting founder approval", lead=lead)
        from app.services import lead_journal as journal
        journal.record(
            lead, db, method=channel, outcome=journal.QUEUED,
            remark=(f"day {day} of the sequence, angle '{angle}'. Eligible: "
                    f"{elig[channel]['reason']}. Awaiting founder approval — "
                    f"nothing has been sent."),
            by="orchestrator")
        return {"action": "PROPOSE", "channel": channel, "angle": angle,
                "day": day,
                "reason": elig[channel]["reason"],
                "note": "requires founder approval before it is sent"}

    decision("orchestrator.next_touch", DONE,
             "every eligible channel in the sequence has been attempted", lead=lead)
    return {"action": "COMPLETE", "channel": None,
            "reason": "every eligible channel in the sequence has been attempted"}


# ------------------------------------------------------------- reporting --

def reachability_report(db, limit: int = 0) -> dict:
    """How many businesses each channel can actually reach, and what blocks
    the rest. The denominator every conversion number needs."""
    from app.models.models import B2BLead
    from collections import Counter

    q = db.query(B2BLead)
    leads = q.limit(limit).all() if limit else q.all()

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

    return {
        "leads": len(leads),
        "suppressed_or_replied": stopped,
        "reachable_on_at_least_one_channel": reach_any,
        "by_channel": dict(counts),
        "top_blockers": {c: blockers[c].most_common(3) for c in CHANNELS},
    }
