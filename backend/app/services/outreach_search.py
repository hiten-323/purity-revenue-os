"""
Outreach search + phone-only conversion engine (V3).

THE NEW IDEA
------------
Geography answers "who can I sell to here". Category answers "which buyer do I
want". Neither answers the question that actually decides what the founder does
in the next hour: "what can I execute right now?"

A business with a verified email and a business with only a phone need different
work, and the pipeline is overwhelmingly the second kind — of 20 businesses on
record, 17 are phone-only and 0 have a usable email. A search that starts from
email describes almost nothing. So outreach method is a first-class dimension
here, computed from stored facts only. Contactability is never manufactured: a
business with no phone and no email is NO_CONTACT_FOUND, not a call target.

THE INVARIANT
-------------
Every active engaged opportunity has EXACTLY ONE next action. set_next_action()
cancels the stale one, creates the replacement, commits, and verifies the count
— in that order, in one function, because the previous defect was cancelling a
reminder and creating nothing, which silently dropped engaged leads out of the
queue. That is what test_never_cancel_without_replacing guards.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# ── Outreach methods: what can actually be done with this business now ───────

METHODS = (
    "ALL", "INTRO_EMAIL", "FOLLOWUP_EMAIL", "EMAIL_READY", "PREVIOUSLY_OPENED",
    "PREVIOUSLY_REPLIED", "WHATSAPP_READY", "PHONE_ONLY", "FOUNDER_CALL",
    "AI_CALL_ELIGIBLE", "SAMPLE", "PROPOSAL", "RE_ENGAGE", "NO_CONTACT_FOUND",
)

# Reaching these means the relationship is closed or suppressed. They must never
# receive a next action — section 21's "must not receive inappropriate actions".
TERMINAL = {"ORDER_WON", "CLOSED_LOST", "NOT_INTERESTED", "DO_NOT_CONTACT"}


def _has_phone(l) -> bool:
    return bool((getattr(l, "phone", "") or "").strip()
                or (getattr(l, "whatsapp_number", "") or "").strip())


def _has_email(l) -> bool:
    return bool((getattr(l, "email", "") or "").strip())


def _email_usable(l) -> bool:
    """
    A guess is not a channel. One definition of usable, shared with the sender.

    email_verification_status == "VALID" is deliberately NOT accepted on its own
    — the same widening removed from Gate A in 413edc3, which reappeared here and
    put 15 unverified addresses back into the INTRO_EMAIL queue. That column has
    been found set on addresses that never passed the verifier, so it cannot
    stand alone. Deferring to contact_trust means the search can never offer a
    recipient the sender would refuse.
    """
    from app.services.contact_trust import sendable, actionable
    return sendable(l)[0] and actionable(l)[0]


def _suppressed(l) -> bool:
    return (bool(getattr(l, "do_not_call", False))
            or (l.status or "") in ("DISQUALIFIED", "DO_NOT_CONTACT"))


def methods_for(lead, ev: dict) -> set[str]:
    """
    Which outreach methods this business genuinely qualifies for.

    `ev` is the real event summary for the lead: emails_sent, opened, replied,
    sample_sent, proposal_sent, last_touch_days. Every branch below reads a
    stored fact — nothing infers that a business is reachable.
    """
    out: set[str] = set()
    if _suppressed(lead):
        return out

    if not _has_phone(lead) and not _has_email(lead):
        return {"NO_CONTACT_FOUND"}

    # ── Email ──
    if _email_usable(lead):
        out.add("EMAIL_READY")
        # An intro is only an intro once, and "once" counts EVERY channel — not
        # just prior emails. A founder call that produced this very address is a
        # relationship, so the next email is a follow-up. Checking emails_sent
        # alone re-qualified a business for an introduction seconds after the
        # founder spoke to them and wrote their address down.
        prior = bool(ev.get("emails_sent")) or ev.get("last_touch_days") is not None
        out.add("FOLLOWUP_EMAIL" if prior else "INTRO_EMAIL")
    if ev.get("opened"):
        out.add("PREVIOUSLY_OPENED")
    if ev.get("replied"):
        out.add("PREVIOUSLY_REPLIED")

    # ── Phone ──
    if _has_phone(lead):
        out.add("FOUNDER_CALL")
        # Phone-only is the program, not merely "has a phone": it is the set the
        # founder must work by voice because email is unavailable or unusable.
        if not _email_usable(lead):
            out.add("PHONE_ONLY")
        if bool(getattr(lead, "phone_verified", False)):
            out.add("WHATSAPP_READY")

    # ── Later stages, only where the event actually happened ──
    if ev.get("sample_sent"):
        out.add("SAMPLE")
    if ev.get("proposal_sent"):
        out.add("PROPOSAL")
    d = ev.get("last_touch_days")
    if d is not None and d >= 30 and not ev.get("replied"):
        out.add("RE_ENGAGE")

    out.add("ALL")
    return out


# ── AI calling readiness: never offered as available when it is not ──────────

def ai_call_status() -> dict:
    import os
    key = (os.getenv("VAPI_API_KEY") or os.getenv("SARVAM_API_KEY") or "").strip()
    if not key:
        return {"available": False, "reason": "AI calling is not configured "
                "(no VAPI/Sarvam key) — Founder Call is available instead"}
    return {"available": True, "reason": "configured"}


# ── Phone-call outcomes → the next action each one earns ─────────────────────
#
# Section 8: "Each outcome creates a DIFFERENT next action." The table IS that
# rule, so the mapping is data rather than branching logic scattered over the
# codebase. `terminal` outcomes end the relationship; `needs` names what the
# founder must capture for the outcome to mean anything.

@dataclass(frozen=True)
class Outcome:
    label: str
    next_action: str | None
    channel: str
    delay_days: float
    note: str
    needs: tuple[str, ...] = ()
    terminal: bool = False


OUTCOMES: dict[str, Outcome] = {
    "NO_ANSWER": Outcome(
        "No answer", "FOUNDER_CALL", "phone", 1,
        "Retry at a different time of day. No answer is not a refusal, and must "
        "never be recorded as one."),
    "BUSY": Outcome(
        "Busy / asked to call back", "FOUNDER_CALL", "phone", 0.5,
        "Retry; if a callback time was given it is stored and used instead.",
        needs=("preferred_contact_time",)),
    "WRONG_NUMBER": Outcome(
        "Wrong number", "VERIFY_CONTACT", "research", 0,
        "The number is wrong, so it is unusable — find a correct one rather than "
        "dialling it again."),
    "GATEKEEPER": Outcome(
        "Reception / gatekeeper", "FOUNDER_CALL", "phone", 1,
        "Do not pitch a gatekeeper. Get the decision maker's name, designation "
        "and the best time, then call back.",
        needs=("decision_maker", "designation", "preferred_contact_time")),
    "WRONG_PERSON": Outcome(
        "Referred to someone else", "FOUNDER_CALL", "phone", 0.5,
        "A referral is progress, not a restart. Record who was named and call them.",
        needs=("decision_maker", "designation")),
    "DECISION_MAKER_FOUND": Outcome(
        "Reached the decision maker", "FOUNDER_CALL", "phone", 0.5,
        "Right person reached — the next call is the qualifying conversation.",
        needs=("decision_maker",)),
    "INTERESTED": Outcome(
        "Interested", "FOUNDER_CALL", "phone", 2,
        "Establish what they actually buy and who decides before pitching further.",
        needs=("decision_maker",)),
    "SEND_DETAILS": Outcome(
        "Asked for details", "FOLLOWUP_EMAIL", "email", 0,
        "They asked, so this is a follow-up that references the call — never an "
        "introduction.",
        needs=("email",)),
    "SEND_WHATSAPP": Outcome(
        "Asked for WhatsApp", "WHATSAPP", "whatsapp", 0,
        "The prospect chose the channel. That is consent for this exchange, and "
        "the message opens on the call, not cold."),
    "SEND_CATALOGUE": Outcome(
        "Asked for the catalogue", "SEND_CATALOGUE", "whatsapp", 0,
        "Send the catalogue on the channel they asked for."),
    "SEND_PRICING": Outcome(
        "Asked for pricing", "SEND_PRICING", "email", 0,
        "Quote against their volumes. MRP is published; the buying price follows "
        "volume."),
    "SAMPLE_REQUESTED": Outcome(
        "Sample requested", "DISPATCH_SAMPLE", "physical", 0,
        "Dispatch is a physical act — it is only ever marked done by the founder "
        "confirming it actually went."),
    "MEETING_REQUESTED": Outcome(
        "Meeting requested", "MEETING_BRIEF", "meeting", 0,
        "Prepare the brief: who, what they buy now, objections, the ask.",
        needs=("next_followup_date",)),
    "CALL_LATER": Outcome(
        "Call later", "FOUNDER_CALL", "phone", 7,
        "Their timing wins. Nothing generic fires in the meantime.",
        needs=("next_followup_date",)),
    "EXISTING_SUPPLIER": Outcome(
        "Already has a supplier", "FOUNDER_CALL", "phone", 3,
        "Not a rejection. Ask what they pay and what they would change; offer a "
        "comparison rather than a switch.",
        needs=("current_supplier",)),
    "PRICE_OBJECTION": Outcome(
        "Price objection", "FOUNDER_CALL", "phone", 2,
        "Establish pack, current price and volume before any discount. Discounts "
        "outside policy need founder approval.",
        needs=("current_supplier",)),
    "NOT_INTERESTED": Outcome(
        "Not interested", None, "none", 0,
        "Closed. No further automated outreach.", terminal=True),
    "DO_NOT_CONTACT": Outcome(
        "Do not contact", None, "none", 0,
        "Permanently suppressed until the founder changes it by hand.",
        terminal=True),
    "OTHER": Outcome(
        "Other", "FOUNDER_CALL", "phone", 3,
        "Founder judgement — the remark carries the detail."),
}


def set_next_action(db, lead, action_type: str | None, due_in_days: float = 0,
                    reason: str = "", margin: float = 0.0) -> dict:
    """
    Make `action_type` the single active next action for this lead.

    Cancel the stale, create the replacement, commit, then VERIFY exactly one
    remains. The order matters: the defect this replaces cancelled a reminder
    and returned without creating anything, so an engaged lead ended up with
    zero next actions and quietly left the founder's queue.

    Passing action_type=None is only correct for a terminal outcome, and then
    leaving zero actions is the right answer rather than a bug.
    """
    from app.models.models import ActionQueue, WorkflowEvent

    stale = db.query(ActionQueue).filter(
        ActionQueue.lead_id == lead.id,
        ActionQueue.status == "PENDING").all()
    for a in stale:
        a.status = "DISMISSED"

    created = None
    if action_type and (lead.status or "") not in TERMINAL:
        created = ActionQueue(
            lead_id=lead.id, action_type=action_type,
            due_date=datetime.utcnow() + timedelta(days=due_in_days),
            expected_margin=margin or 0.0, status="PENDING")
        db.add(created)

    db.add(WorkflowEvent(
        lead_id=lead.id, event_type="NEXT_ACTION_SET", actor="SYSTEM",
        channel="workflow",
        payload={"cancelled": [a.action_type for a in stale],
                 "created": action_type, "reason": reason},
        occurred_at=datetime.utcnow()))
    db.commit()

    # Verify, do not assume. This is the assertion the regression test relies on.
    active = db.query(ActionQueue).filter(
        ActionQueue.lead_id == lead.id,
        ActionQueue.status == "PENDING").all()
    expected = 1 if (action_type and (lead.status or "") not in TERMINAL) else 0
    return {"cancelled": len(stale), "active_now": len(active),
            "action": action_type, "ok": len(active) == expected}


def apply_call_outcome(db, lead, outcome_key: str, captured: dict | None = None) -> dict:
    """
    Record a real founder-call outcome and move the relationship one step.

    `captured` holds what the founder actually learned on the call — decision
    maker, supplier, an email offered, a callback time. It is written to Business
    Memory as a LeadInteraction so the next draft can use it, and an email
    captured here carries FOUNDER_CALL provenance so the follow-up knows the
    relationship already exists.
    """
    from app.models.models import LeadInteraction, WorkflowEvent

    key = (outcome_key or "").upper().strip()
    if key not in OUTCOMES:
        raise ValueError(f"unknown outcome '{outcome_key}' — expected one of "
                         f"{sorted(OUTCOMES)}")
    oc = OUTCOMES[key]
    cap = captured or {}

    i = LeadInteraction(
        lead_id=lead.id, occurred_at=datetime.utcnow(), created_by="FOUNDER",
        method="founder_call", outcome=key,
        decision_maker=cap.get("decision_maker"), designation=cap.get("designation"),
        current_supplier=cap.get("current_supplier"),
        preferred_contact_time=cap.get("preferred_contact_time"),
        preferred_contact_method=cap.get("preferred_contact_method"),
        next_followup_date=cap.get("next_followup_date"),
        monthly_consumption_kg=cap.get("monthly_consumption_kg"),
        interested=True if key in ("INTERESTED", "DECISION_MAKER_FOUND",
                                   "SAMPLE_REQUESTED", "MEETING_REQUESTED")
        else (False if key in ("NOT_INTERESTED", "DO_NOT_CONTACT") else None),
        remark=cap.get("remark"))
    db.add(i)

    promoted = []
    for f in ("decision_maker", "current_supplier", "next_followup_date"):
        if cap.get(f) and hasattr(lead, f):
            setattr(lead, f, cap[f]); promoted.append(f)

    # An email given on a call is a real, attributable address — and the reason
    # the next email must be a follow-up rather than an introduction.
    if cap.get("email"):
        lead.email = cap["email"].strip()
        lead.email_verification_status = "FOUNDER_CALL_PROVIDED"
        lead.email_verified = True
        promoted.append("email")
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="CONTACT_CAPTURED", actor="FOUNDER",
            channel="founder_call",
            payload={"email": cap["email"], "source": "FOUNDER_CALL",
                     "note": "given by the prospect on a call — attributable, "
                             "so the next email is a follow-up not an intro"},
            occurred_at=datetime.utcnow()))

    if key == "DO_NOT_CONTACT":
        lead.do_not_call = True
        lead.status = "DO_NOT_CONTACT"
    elif key == "NOT_INTERESTED":
        lead.status = "CLOSED_LOST"
    elif key in ("INTERESTED", "DECISION_MAKER_FOUND"):
        lead.status = "QUALIFIED"
    elif key == "SAMPLE_REQUESTED":
        lead.status = "SAMPLE_REQUESTED"
    elif key == "MEETING_REQUESTED":
        lead.status = "MEETING_BOOKED"

    db.add(WorkflowEvent(
        lead_id=lead.id, event_type="FOUNDER_CALL", actor="FOUNDER", channel="phone",
        payload={"outcome": key, "captured": {k: v for k, v in cap.items() if v},
                 "promoted": promoted},
        occurred_at=datetime.utcnow()))
    db.commit()

    nxt = set_next_action(db, lead, oc.next_action, oc.delay_days,
                          reason=f"call outcome {key}")
    missing = [f for f in oc.needs if not cap.get(f)]
    return {
        "outcome": key, "label": oc.label, "guidance": oc.note,
        "interaction_id": i.id, "promoted": promoted,
        "next_action": oc.next_action, "channel": oc.channel,
        "due_in_days": oc.delay_days, "terminal": oc.terminal,
        "next_action_state": nxt,
        # Surfaced rather than silently ignored: the outcome is recorded either
        # way, but an unanswered field is a fact we still do not have.
        "not_captured": missing,
    }


# ── Power Hour: rank the calls worth making, not every number on file ────────

def rank_calls(leads, ev_by_lead: dict, limit: int = 15) -> list[dict]:
    """
    Order phone-reachable businesses by what makes a call worth the founder's
    minutes. Modelled margin contributes but never dominates — section 6 — so a
    business we know something about outranks a bigger unknown.
    """
    out = []
    for l in leads:
        if _suppressed(l) or not _has_phone(l):
            continue
        ev = ev_by_lead.get(l.id, {})
        score, why = 0.0, []
        if ev.get("replied"):
            score += 40; why.append("has replied before")
        if getattr(l, "decision_maker", None):
            score += 20; why.append("decision maker known")
        if getattr(l, "current_supplier", None):
            score += 12; why.append("we know who supplies them")
        if getattr(l, "phone_verified", False):
            score += 10; why.append("phone verified")
        rv = getattr(l, "maps_reviews_count", None)
        if rv:
            score += min(12.0, rv / 50.0); why.append(f"{rv} reviews — real footfall")
        d = ev.get("last_touch_days")
        if d is None:
            score += 8; why.append("never contacted")
        elif d >= 14:
            score += 6; why.append(f"quiet {d} days")
        # Margin is a tiebreaker at ~15 points maximum, deliberately.
        score += min(15.0, (getattr(l, "estimated_value", 0) or 0) * 0.31 / 4000.0)
        out.append({"lead_id": l.id, "company": l.company, "city": l.city,
                    "category": l.division, "phone": l.phone or l.whatsapp_number,
                    "score": round(score, 1), "why": why,
                    "last_contact_days": d,
                    "decision_maker": getattr(l, "decision_maker", None)})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


# ── Rates: refuse to compute a rate from a handful of events ─────────────────

MIN_OBS = 20


def rate(numerator: int, denominator: int, label: str) -> dict:
    """
    A conversion rate on 3 sends is noise wearing a percentage sign. Below
    MIN_OBS observations this reports INSUFFICIENT DATA and no number at all.
    """
    if denominator < MIN_OBS:
        return {"label": label, "status": "INSUFFICIENT_DATA",
                "observations": denominator, "needed": MIN_OBS, "rate": None}
    return {"label": label, "status": "OK", "observations": denominator,
            "rate": round(numerator / denominator, 4)}


def get_switching_signal(db, lead_id: int) -> dict:
    """
    Determine willingness to switch by checking Business Memory interactions.
    Returns {"signal": "CONFIRMED"|"POSITIVE SIGNAL"|"UNKNOWN"|"NEGATIVE", "source": str|None}
    """
    from app.models.models import LeadInteraction
    
    interactions = db.query(LeadInteraction).filter(
        LeadInteraction.lead_id == lead_id,
        LeadInteraction.superseded_by_id.is_(None)
    ).order_by(LeadInteraction.occurred_at.desc()).all()
    
    for inter in interactions:
        rem = (inter.remark or "").lower()
        outc = (inter.outcome or "").upper()
        method_label = "Founder Call" if inter.method == "founder_call" else ("Email Reply" if inter.method == "email" else inter.method.title())
        date_str = inter.occurred_at.strftime("%d %b %Y")
        
        # 1. CONFIRMED switching
        if outc in ("EXISTING_SUPPLIER", "PRICE_OBJECTION", "INTERESTED") and any(w in rem for w in ("willing to", "switch", "evaluate", "try", "testing", "test")):
            return {
                "signal": "CONFIRMED",
                "source": f"{method_label} · {date_str}",
                "detail": inter.remark
            }
            
        # 2. POSITIVE SIGNAL
        if outc in ("INTERESTED", "SEND_DETAILS", "SEND_PRICING", "SEND_CATALOGUE", "SAMPLE_REQUESTED") or any(w in rem for w in ("margin", "pricing", "sample", "catalogue", "catalog")):
            return {
                "signal": "POSITIVE SIGNAL",
                "source": f"{method_label} · {date_str}",
                "detail": inter.remark
            }
            
        # 3. NEGATIVE
        if outc in ("NOT_INTERESTED", "DO_NOT_CONTACT") or any(w in rem for w in ("exclusive contract", "not considering", "no interest", "don't want")):
            return {
                "signal": "NEGATIVE",
                "source": f"{method_label} · {date_str}",
                "detail": inter.remark
            }
            
    return {
        "signal": "UNKNOWN",
        "source": None,
        "detail": None
    }


def get_engagement_level(lead, ev: dict) -> str:
    """
    Determine explicit engagement level: HOT, WARM, COLD, UNKNOWN.
    """
    stat = (lead.status or "DISCOVERED").upper()
    if stat in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED") or ev.get("replied"):
        return "HOT"
    if stat in ("SAMPLE_SENT", "PROPOSAL_SENT") or ev.get("sample_sent") or ev.get("proposal_sent"):
        return "HOT"
        
    opens = ev.get("opened", 0) or getattr(lead, "email_opens", 0) or 0
    clicks = ev.get("clicks", 0) or getattr(lead, "email_clicks", 0) or 0
    dm = getattr(lead, "decision_maker", None)
    
    if opens > 1 or clicks > 0 or dm:
        return "WARM"
    if opens == 1:
        return "WARM"
    if getattr(lead, "email_verified", False) and ev.get("emails_sent", 0) > 0:
        return "COLD"
        
    return "UNKNOWN"


def get_segment_performance(db, state: str, category: str, method: str) -> dict:
    """
    Calculate conversion performance for state + category + method.
    Using MIN_OBS = 20.
    """
    from app.models.models import B2BLead
    
    q = db.query(B2BLead).filter(B2BLead.status != "DISQUALIFIED")
    
    # Resolve state match
    if state:
        if state.lower().strip() == "punjab":
            q = q.filter(
                B2BLead.state.ilike("%punjab%") | 
                B2BLead.city.in_(["abohar", "chandigarh", "mohali", "panchkula", "zirakpur", "kharar", "bathinda", "ludhiana", "amritsar", "jalandhar", "patiala"])
            )
        else:
            q = q.filter(B2BLead.state.ilike("%" + state.strip() + "%"))
            
    if category and category.upper() not in ("ALL", ""):
        q = q.filter(B2BLead.division == category.strip())
        
    leads = q.all()
    
    sent_count = 0
    qualified_count = 0
    
    for l in leads:
        # Check if contacted
        is_contacted = False
        if method.upper() == "EMAIL":
            # Check emails_sent status
            is_contacted = l.status in ("EMAIL_SENT", "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED")
        else:
            is_contacted = l.status != "DISCOVERED"
            
        if is_contacted:
            sent_count += 1
            if l.status in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED"):
                qualified_count += 1
                
    if sent_count < MIN_OBS:
        return {
            "status": "INSUFFICIENT_DATA",
            "rate": None,
            "observations": sent_count,
            "needed": MIN_OBS
        }
        
    return {
        "status": "OK",
        "rate": round((qualified_count / sent_count) * 100, 1),
        "observations": sent_count
    }


def get_conversion_priority_score(lead, ev: dict, switching_signal: dict, engagement_level: str, segment_perf: dict) -> float:
    """
    Calculate numerical priority score in range [0, 100] based on direct intent, switching signal, 
    engagement level, segment conversion performance, and potential commercial value.
    """
    score = 0.0
    
    # 1. Engagement Level (Max 40.0)
    score += {"HOT": 40.0, "WARM": 20.0, "COLD": 5.0, "UNKNOWN": 0.0}.get(engagement_level, 0.0)
    
    # 2. Switching Propensity (Max 25.0)
    score += {"CONFIRMED": 25.0, "POSITIVE SIGNAL": 15.0, "UNKNOWN": 0.0, "NEGATIVE": -50.0}.get(switching_signal.get("signal"), 0.0)
    
    # 3. Direct Lead Status Intent (Max 15.0)
    stat = (lead.status or "DISCOVERED").upper()
    if stat in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED"):
        score += 15.0
    elif stat in ("SAMPLE_SENT", "PROPOSAL_SENT"):
        score += 10.0
        
    # 4. Buying Fit Classification (Max 10.0)
    fit_score = lead.coffee_buying_score or 0
    if fit_score >= 75:
        score += 10.0
    elif fit_score >= 45:
        score += 5.0
        
    # 5. Segment Conversion Rate (Max 5.0)
    if segment_perf.get("status") == "OK" and segment_perf.get("rate") is not None:
        score += (segment_perf["rate"] / 100.0) * 5.0
        
    # 6. Commercial Value potential (Max 5.0)
    val = lead.estimated_value or lead.estimated_annual_value or 60000.0
    score += min(val / 300000.0, 5.0)
    
    return round(max(score, 0.0), 2)


