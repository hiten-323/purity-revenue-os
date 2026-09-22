"""Intelligence report — delivery and success are separate."""
from __future__ import annotations
from typing import Any
from sqlalchemy.orm import Session
from app.services.outreach_intelligence.models import (
    OutreachEvent, OutreachExperienceAggregate, OutreachOutcomeCorrection,
)
try:
    from app.services.outreach_learning import MIN_EVIDENCE
except Exception:  # noqa: BLE001
    MIN_EVIDENCE = 5


def build_intelligence_report(db: Session) -> dict[str, Any]:
    events = db.query(OutreachEvent).all()
    email_funnel = {"sent": 0, "delivered": 0, "replied": 0, "positive": 0, "commercial": 0, "unsubscribe": 0, "bounce": 0}
    call_funnel = {"dialled": 0, "answered": 0, "positive": 0, "commercial": 0, "callback_or_meeting": 0, "dnc": 0}

    for e in events:
        ch = (e.channel or "").lower()
        et = (e.event_type or "").upper()
        ds = (e.delivery_status or "").upper()
        outcome = (e.outcome or "").upper()

        if ch == "email" or "EMAIL" in et:
            if et.endswith("SENT") or ds in {"SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ"}:
                email_funnel["sent"] += 1
            if ds in {"DELIVERED", "READ"}:
                email_funnel["delivered"] += 1
            if "REPLY" in et or (e.response_status or "").upper() == "REPLIED":
                email_funnel["replied"] += 1
            if outcome in {"INTERESTED", "POSITIVE", "SAMPLE_REQUESTED", "CATALOGUE_REQUESTED", "PRICING_REQUESTED", "MEETING_REQUESTED", "CALLBACK"}:
                email_funnel["positive"] += 1
            if outcome in {"PRICING_REQUESTED", "CATALOGUE_REQUESTED", "SAMPLE_REQUESTED"}:
                email_funnel["commercial"] += 1
            if outcome in {"UNSUBSCRIBE", "DO_NOT_CONTACT"}:
                email_funnel["unsubscribe"] += 1
            if outcome in {"BOUNCE", "HARD_BOUNCE"} or "BOUNCE" in et:
                email_funnel["bounce"] += 1

        if ch == "call" or "CALL" in et:
            call_funnel["dialled"] += 1
            if outcome and outcome not in {"NO_ANSWER", "FAILED", "BUSY"}:
                call_funnel["answered"] += 1
            if outcome in {"INTERESTED", "SEND_DETAILS", "SEND_PRICING", "SAMPLE_REQUESTED", "MEETING_REQUESTED", "CALLBACK", "PRICE_OBJECTION", "EXISTING_SUPPLIER"}:
                call_funnel["positive"] += 1
            if outcome in {"SEND_PRICING", "SEND_DETAILS", "SAMPLE_REQUESTED", "PRICE_OBJECTION"}:
                call_funnel["commercial"] += 1
            if outcome in {"CALLBACK", "MEETING_REQUESTED", "CALL_LATER"}:
                call_funnel["callback_or_meeting"] += 1
            if outcome == "DO_NOT_CONTACT":
                call_funnel["dnc"] += 1

    aggregates = [{
        "segment_key": a.segment_key, "channel": a.channel, "metric": a.metric,
        "value": a.value, "sample_size": a.sample_size, "wins": a.wins,
        "promoted": (a.sample_size or 0) >= MIN_EVIDENCE,
    } for a in db.query(OutreachExperienceAggregate).all()]

    patterns = []
    try:
        from app.models.models import LearnedPattern
        for row in db.query(LearnedPattern).order_by(LearnedPattern.updated_at.desc()).limit(50).all():
            patterns.append({
                "scope": row.scope, "key": row.key, "metric": row.metric,
                "value": row.value, "sample_size": row.sample_size, "wins": row.wins,
                "promoted": (row.sample_size or 0) >= MIN_EVIDENCE,
            })
    except Exception:  # noqa: BLE001
        pass

    return {
        "email_funnel": email_funnel,
        "call_funnel": call_funnel,
        "sequence_summaries": {
            "note": "Touch-level sequences via OutreachTouch; not expanded in V1 report.",
            "ledger_events": len(events),
        },
        "what_we_learned": {
            "aggregates": aggregates,
            "patterns": patterns,
            "min_evidence": MIN_EVIDENCE,
        },
        "corrections_count": db.query(OutreachOutcomeCorrection).count(),
        "known_limitations": [
            "Delivery is not success — funnels separate sent/delivered/replied/positive.",
            f"Patterns require MIN_EVIDENCE={MIN_EVIDENCE} before promotion.",
            "Learning is advisory only; kill switches and eligibility are untouched.",
            "WhatsApp remains off; SMART/AUTO/AI_CALLING flags are not modified.",
            "Call transcripts live primarily in the voice sidecar — V1 uses lead/outcome fields.",
            "Day-1 ledger is largely mirrored from WorkflowEvent/OutreachTouch (sync_from_existing).",
        ],
        "safety": {
            "advisory_only": True,
            "does_not_send": True,
            "does_not_dial": True,
            "does_not_enable_flags": True,
        },
    }
