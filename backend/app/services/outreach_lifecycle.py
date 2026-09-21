"""Lifecycle memory and learning for automatic B2B outreach.

This module does not create a second decision engine. It feeds observed
interaction history into smart_outreach.plan_touch(), records intent,
and learns measured conversion patterns from the real lifecycle.

Automatic means no founder approval is required for ordinary outreach. Provider
and compliance gates remain absolute: email trust/deliverability gates and the
WhatsApp consent/template gate cannot be bypassed here.
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import B2BLead, LearnedPattern, WorkflowEvent
from app.services.smart_outreach import (
    OutreachProfile,
    OutreachTouch,
    classify_lead,
    plan_touch,
    execute_one,
    select_candidates,
)

POSITIVE_INTENTS = {
    "CATALOGUE_REQUESTED",
    "PRICING_REQUESTED",
    "SAMPLE_REQUESTED",
    "MEETING_REQUESTED",
    "CALLBACK",
    "INTERESTED",
    "NEGOTIATION",
}
NEGATIVE_INTENTS = {"NOT_INTERESTED", "OPTED_OUT", "DO_NOT_CONTACT", "COMPLAINT", "BOUNCED"}
MACHINE_INTENTS = {"OUT_OF_OFFICE", "MACHINE_REPLY"}


def ensure_schema() -> None:
    from app.database.database import Base, engine
    import app.services.smart_outreach  # noqa: F401

    Base.metadata.create_all(bind=engine)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{k}:{v}" for k, v in value.items()).lower()
    return str(value).lower()


def infer_intent(event: WorkflowEvent) -> str:
    """Classify inbound text. Machine replies are not INTERESTED.

    reply_intelligence.classify_sender runs first so an out-of-office cannot
    exit the cadence or lift the account cap.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    body = " ".join(str(payload.get(k) or "") for k in ("body", "text", "snippet", "message"))
    subject = str(payload.get("subject") or "")
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    text = _text(event.event_type) + " " + _text(event.payload)

    try:
        from app.services.reply_intelligence import classify_sender
        sender = classify_sender(subject, body or text, headers)
        if sender.get("sender") == "MACHINE":
            kind = sender.get("kind") or "AUTO_RESPONDER"
            if kind == "BOUNCE":
                return "BOUNCED"
            if kind == "OUT_OF_OFFICE":
                return "OUT_OF_OFFICE"
            return "MACHINE_REPLY"
    except Exception:
        pass

    # Opt-out / stop language first — "remove me" is OPTED_OUT, not a soft no.
    if any(
        x in text
        for x in (
            "unsubscribe",
            "opted_out",
            "opt out",
            "do_not_contact",
            "do not contact",
            "remove me",
            "stop emailing",
            "stop messaging",
        )
    ):
        return "OPTED_OUT"
    if any(x in text for x in ("not interested", "no thanks", "no thank you")):
        return "NOT_INTERESTED"
    if any(x in text for x in ("wrong person", "wrong contact", "not the right person", "don't handle", "do not handle")):
        return "WRONG_PERSON"
    if any(x in text for x in ("already buy", "already have a supplier", "existing supplier", "current supplier")):
        return "EXISTING_SUPPLIER"
    if any(x in text for x in ("next week", "next month", "call later", "after diwali", "not now")):
        return "CALL_LATER"
    if any(x in text for x in ("price", "pricing", "rate", "wholesale", "best price", "moq")):
        return "PRICING_REQUESTED"
    if any(x in text for x in ("catalogue", "catalog", "send it", "send details", "share details", "yes send")):
        return "CATALOGUE_REQUESTED"
    if any(x in text for x in ("sample", "trial", "taste")):
        return "SAMPLE_REQUESTED"
    if any(x in text for x in ("call me", "call back", "callback", "speak", "phone")):
        return "CALLBACK"
    if any(x in text for x in ("meeting", "demo", "let's talk", "lets talk")):
        return "MEETING_REQUESTED"
    if event.event_type in ("EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY") or "reply" in text:
        return "INTERESTED"
    return "NONE"


def _latest_inbound_events(db: Session, since_hours: int = 48) -> list[WorkflowEvent]:
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)
    return (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.occurred_at >= cutoff)
        .filter(
            WorkflowEvent.event_type.in_(
                [
                    "EMAIL_REPLY_RECEIVED",
                    "REPLY_RECEIVED",
                    "WHATSAPP_REPLY",
                    "UNSUBSCRIBED",
                    "OPTED_OUT",
                    "DO_NOT_CONTACT",
                    "COMPLAINT",
                ]
            )
        )
        .order_by(WorkflowEvent.occurred_at.asc())
        .all()
    )


def sync_inbound_memory(db: Session, since_hours: int = 48) -> dict:
    ensure_schema()
    events = _latest_inbound_events(db, since_hours=since_hours)
    updated = 0
    by_intent: Counter[str] = Counter()
    for event in events:
        if not event.lead_id:
            continue
        lead = db.query(B2BLead).filter(B2BLead.id == event.lead_id).first()
        if not lead:
            continue
        intent = infer_intent(event)
        if intent == "NONE":
            continue
        profile = classify_lead(db, lead)
        profile.last_intent = intent
        profile.intent = intent
        profile.last_channel = (event.channel or "").lower() or profile.last_channel
        if intent in NEGATIVE_INTENTS:
            profile.warmth = "COOLDOWN"
            lead.contact_status = "OPTED_OUT" if intent == "OPTED_OUT" else (
                "BOUNCED" if intent == "BOUNCED" else "DO_NOT_CONTACT"
            )
            lead.contact_status_reason = f"inbound intent: {intent}"
        elif intent in MACHINE_INTENTS or intent == "CALL_LATER":
            profile.warmth = "CONTACTED"
        elif intent in ("PRICING_REQUESTED", "NEGOTIATION"):
            profile.warmth = "HOT"
        elif intent in POSITIVE_INTENTS:
            profile.warmth = "WARM"
        profile.next_action = plan_touch(db, lead, profile).get("action")
        profile.next_action_at = datetime.utcnow()
        by_intent[intent] += 1
        updated += 1
    if updated:
        db.commit()
    return {"events_seen": len(events), "profiles_updated": updated, "intents": dict(by_intent)}


def _metric_row(db: Session, category: str, channel: str) -> dict:
    touches = (
        db.query(OutreachTouch)
        .join(OutreachProfile, OutreachTouch.profile_id == OutreachProfile.id)
        .filter(OutreachProfile.category == category, OutreachTouch.channel == channel)
        .all()
    )
    lead_ids = {t.lead_id for t in touches}
    replies = 0
    if lead_ids:
        replies = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.lead_id.in_(lead_ids))
            .filter(
                WorkflowEvent.event_type.in_(
                    ("EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY")
                )
            )
            .count()
        )
    sent = sum(1 for t in touches if t.status in ("SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ"))
    return {"sent": sent, "replies": replies, "reply_rate": (replies / sent * 100.0) if sent else 0.0}


def learn_from_lifecycle(db: Session, min_sample: int = 5) -> dict:
    """Compatibility wrapper for the unified email + AI-call learning loop."""
    from app.services.outreach_learning import rebuild_learning

    result = rebuild_learning(db, min_sample=min_sample)
    db.commit()
    return result



def _best_channel(db: Session, category: str) -> str:
    candidates = []
    for channel in ("email", "whatsapp"):
        row = (
            db.query(LearnedPattern)
            .filter(
                LearnedPattern.scope == "category_channel",
                LearnedPattern.key == f"{category}:{channel}",
                LearnedPattern.metric == "reply_rate_pct",
            )
            .first()
        )
        if row and row.sample_size >= 5:
            candidates.append((float(row.value), channel))
    return max(candidates, default=(0.0, "email"))[1]


def run_automatic_cycle(db: Session, limit: int = 20) -> dict:
    """Run the canonical multi-channel cycle and then learn from its outcomes.

    This wrapper deliberately delegates execution to smart_outreach.run_cycle.
    Older code here executed only one preferred channel, which contradicted the
    mandatory independent email + AI-call policy. The canonical cycle now owns
    both-channel execution and safety gates.
    """
    ensure_schema()
    memory = sync_inbound_memory(db)

    from app.services.smart_outreach import run_cycle
    execution = run_cycle(db, limit=limit)

    learning = learn_from_lifecycle(db)
    return {
        "memory": memory,
        "processed": execution.get("processed", 0),
        "results": execution.get("results", []),
        "execution": execution,
        "learning": learning,
    }

