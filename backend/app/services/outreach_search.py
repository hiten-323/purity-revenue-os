"""
Outreach search + phone-only conversion engine (V3).

See module body for method classification, call outcomes, and ranking.

Fail-closed decision authority is installed at the bottom of this file so both
the API and the worker process get it without depending on FastAPI startup.
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

TERMINAL = {"ORDER_WON", "CLOSED_LOST", "NOT_INTERESTED", "DO_NOT_CONTACT"}


def _has_phone(l) -> bool:
    return bool((getattr(l, "phone", "") or "").strip()
                or (getattr(l, "whatsapp_number", "") or "").strip())


def _has_email(l) -> bool:
    return bool((getattr(l, "email", "") or "").strip())


def _email_usable(l) -> bool:
    from app.services.contact_trust import sendable, actionable
    return sendable(l)[0] and actionable(l)[0]


def _suppressed(l) -> bool:
    return (bool(getattr(l, "do_not_call", False))
            or (l.status or "") in ("DISQUALIFIED", "DO_NOT_CONTACT"))


def methods_for(lead, ev: dict) -> set[str]:
    out: set[str] = set()
    if _suppressed(lead):
        return out

    if not _has_phone(lead) and not _has_email(lead):
        return {"NO_CONTACT_FOUND"}

    if _email_usable(lead):
        out.add("EMAIL_READY")
        prior = bool(ev.get("emails_sent")) or ev.get("last_touch_days") is not None
        out.add("FOLLOWUP_EMAIL" if prior else "INTRO_EMAIL")
    if ev.get("opened"):
        out.add("PREVIOUSLY_OPENED")
    if ev.get("replied"):
        out.add("PREVIOUSLY_REPLIED")

    if _has_phone(lead):
        out.add("FOUNDER_CALL")
        if not _email_usable(lead):
            out.add("PHONE_ONLY")
        if bool(getattr(lead, "phone_verified", False)):
            out.add("WHATSAPP_READY")

    if ev.get("sample_sent"):
        out.add("SAMPLE")
    if ev.get("proposal_sent"):
        out.add("PROPOSAL")
    d = ev.get("last_touch_days")
    if d is not None and d >= 30 and not ev.get("replied"):
        out.add("RE_ENGAGE")

    out.add("ALL")
    return out


def ai_call_status() -> dict:
    """Report the actual configured voice path, not a retired provider."""
    from app.services import voice_router

    ok, detail = voice_router.config_status()
    if not ok:
        return {"available": False, "reason": detail}
    if voice_router.kill_switch_engaged():
        return {"available": False, "reason": "AI calling kill switch is engaged"}
    return {"available": True, "reason": detail}


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

    active = db.query(ActionQueue).filter(
        ActionQueue.lead_id == lead.id,
        ActionQueue.status == "PENDING").all()
    expected = 1 if (action_type and (lead.status or "") not in TERMINAL) else 0
    return {"cancelled": len(stale), "active_now": len(active),
            "action": action_type, "ok": len(active) == expected}


def apply_call_outcome(db, lead, outcome_key: str, captured: dict | None = None) -> dict:
    """
    Record a real founder-call outcome and move the relationship one step.

    Next action comes only from decide_after_call. On engine failure this queues
    FOUNDER_REVIEW — never the local OUTCOMES registry default.
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

    try:
        from app.services.phone_intelligence import decide_after_call
        decided = decide_after_call(lead, db, key, cap, cap.get("remark") or "")
        action_type, delay = decided["action"], oc.delay_days
        if action_type in ("NONE", "WAIT"):
            action_type = None
        reason = f"call outcome {key} -> {decided['decided_by']}"
        if decided.get("blocked"):
            reason += f" (blocked: {decided['blocked']})"
    except Exception as e:
        # Fail closed. Registry is presentation only — not a second engine.
        action_type, delay = "FOUNDER_REVIEW", 0
        reason = (f"call outcome {key} — decision engine unavailable "
                  f"({e.__class__.__name__}: {e}); FOUNDER_REVIEW, not registry")

    nxt = set_next_action(db, lead, action_type, delay, reason=reason)
    missing = [f for f in oc.needs if not cap.get(f)]
    return {
        "outcome": key, "label": oc.label, "guidance": oc.note,
        "interaction_id": i.id, "promoted": promoted,
        "next_action": action_type, "channel": oc.channel,
        "due_in_days": oc.delay_days, "terminal": oc.terminal,
        "next_action_state": nxt,
        "not_captured": missing,
    }


def rank_calls(leads, ev_by_lead: dict, limit: int = 15) -> list[dict]:
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
        score += min(15.0, (getattr(l, "estimated_value", 0) or 0) * 0.31 / 4000.0)
        out.append({"lead_id": l.id, "company": l.company, "city": l.city,
                    "category": l.division, "phone": l.phone or l.whatsapp_number,
                    "score": round(score, 1), "why": why,
                    "last_contact_days": d,
                    "decision_maker": getattr(l, "decision_maker", None)})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


MIN_OBS = 20


def rate(numerator: int, denominator: int, label: str) -> dict:
    if denominator < MIN_OBS:
        return {"label": label, "status": "INSUFFICIENT_DATA",
                "observations": denominator, "needed": MIN_OBS, "rate": None}
    return {"label": label, "status": "OK", "observations": denominator,
            "rate": round(numerator / denominator, 4)}


def get_switching_signal(db, lead_id: int) -> dict:
    from app.models.models import LeadInteraction

    interactions = db.query(LeadInteraction).filter(
        LeadInteraction.lead_id == lead_id,
        LeadInteraction.superseded_by_id.is_(None)
    ).order_by(LeadInteraction.occurred_at.desc()).all()

    for inter in interactions:
        rem = (inter.remark or "").lower()
        outc = (inter.outcome or "").upper()
        method_label = "Founder Call" if inter.method == "founder_call" else (
            "Email Reply" if inter.method == "email" else inter.method.title())
        date_str = inter.occurred_at.strftime("%d %b %Y")

        if outc in ("EXISTING_SUPPLIER", "PRICE_OBJECTION", "INTERESTED") and any(
                w in rem for w in ("willing to", "switch", "evaluate", "try", "testing", "test")):
            return {"signal": "CONFIRMED", "source": f"{method_label} · {date_str}",
                    "detail": inter.remark}
        if outc in ("INTERESTED", "SEND_DETAILS", "SEND_PRICING", "SEND_CATALOGUE",
                    "SAMPLE_REQUESTED") or any(
                w in rem for w in ("margin", "pricing", "sample", "catalogue", "catalog")):
            return {"signal": "POSITIVE SIGNAL", "source": f"{method_label} · {date_str}",
                    "detail": inter.remark}
        if outc in ("NOT_INTERESTED", "DO_NOT_CONTACT") or any(
                w in rem for w in ("exclusive contract", "not considering", "no interest", "don't want")):
            return {"signal": "NEGATIVE", "source": f"{method_label} · {date_str}",
                    "detail": inter.remark}

    return {"signal": "UNKNOWN", "source": None, "detail": None}


def get_engagement_level(lead, ev: dict) -> str:
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
    from app.models.models import B2BLead

    q = db.query(B2BLead).filter(B2BLead.status != "DISQUALIFIED")
    if state:
        if state.lower().strip() == "punjab":
            q = q.filter(
                B2BLead.state.ilike("%punjab%") |
                B2BLead.city.in_(["abohar", "chandigarh", "mohali", "panchkula",
                                  "zirakpur", "kharar", "bathinda", "ludhiana",
                                  "amritsar", "jalandhar", "patiala"])
            )
        else:
            q = q.filter(B2BLead.state.ilike("%" + state.strip() + "%"))
    if category and category.upper() not in ("ALL", ""):
        q = q.filter(B2BLead.division == category.strip())

    leads = q.all()
    sent_count = 0
    qualified_count = 0
    for l in leads:
        is_contacted = False
        if method.upper() == "EMAIL":
            is_contacted = l.status in (
                "EMAIL_SENT", "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED",
                "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED")
        else:
            is_contacted = l.status != "DISCOVERED"
        if is_contacted:
            sent_count += 1
            if l.status in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED",
                            "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED"):
                qualified_count += 1

    if sent_count < MIN_OBS:
        return {"status": "INSUFFICIENT_DATA", "rate": None,
                "observations": sent_count, "needed": MIN_OBS}
    return {"status": "OK", "rate": round((qualified_count / sent_count) * 100, 1),
            "observations": sent_count}


def get_conversion_priority_score(lead, ev: dict, switching_signal: dict,
                                  engagement_level: str, segment_perf: dict) -> float:
    score = 0.0
    score += {"HOT": 40.0, "WARM": 20.0, "COLD": 5.0, "UNKNOWN": 0.0}.get(
        engagement_level, 0.0)
    score += {"CONFIRMED": 25.0, "POSITIVE SIGNAL": 15.0, "UNKNOWN": 0.0,
              "NEGATIVE": -50.0}.get(switching_signal.get("signal"), 0.0)
    stat = (lead.status or "DISCOVERED").upper()
    if stat in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED"):
        score += 15.0
    elif stat in ("SAMPLE_SENT", "PROPOSAL_SENT"):
        score += 10.0
    fit_score = lead.coffee_buying_score or 0
    if fit_score >= 75:
        score += 10.0
    elif fit_score >= 45:
        score += 5.0
    if segment_perf.get("status") == "OK" and segment_perf.get("rate") is not None:
        score += (segment_perf["rate"] / 100.0) * 5.0
    val = lead.estimated_value or lead.estimated_annual_value or 60000.0
    score += min(val / 300000.0, 5.0)
    return round(max(score, 0.0), 2)
