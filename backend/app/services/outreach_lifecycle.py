"""Lifecycle memory and learning for automatic B2B outreach.

This module does not create a second decision engine.  It feeds observed
interaction history into smart_outreach.evaluate_next_action(), records intent,
and learns measured conversion patterns from the real lifecycle.

Automatic means no founder approval is required for ordinary outreach.  Provider
and compliance gates remain absolute: email trust/deliverability gates and the
WhatsApp consent/template gate cannot be bypassed here.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import B2BLead, LearnedPattern, WorkflowEvent
from app.services.smart_outreach import (
    OutreachProfile,
    OutreachTouch,
    classify_lead,
    evaluate_next_action,
    execute_one,
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
NEGATIVE_INTENTS = {"NOT_INTERESTED", "OPTED_OUT", "DO_NOT_CONTACT", "COMPLAINT"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{k}:{v}" for k, v in value.items()).lower()
    return str(value).lower()


def infer_intent(event: WorkflowEvent) -> str:
    """Classify an observed inbound event without inventing facts."""
    text = _text(event.event_type) + " " + _text(event.payload)
    if any(x in text for x in ("unsubscribe", "opted_out", "do_not_contact", "remove me", "not interested")):
        return "OPTED_OUT" if "unsubscribe" in text or "opted_out" in text else "NOT_INTERESTED"
    if any(x in text for x in ("price", "pricing", "rate", "wholesale", "best price", "moq")):
        return "PRICING_REQUESTED"
    if any(x in text for x in ("catalogue", "catalog", "send it", "send details", "share details")):
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
        .filter(WorkflowEvent.event_type.in_(
            [
                "EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY",
                "UNSUBSCRIBED", "OPTED_OUT", "DO_NOT_CONTACT", "COMPLAINT",
            ]
        ))
        .order_by(WorkflowEvent.occurred_at.asc())
        .all()
    )


def sync_inbound_memory(db: Session, since_hours: int = 48) -> dict:
    """Turn inbound events into durable lead/profile memory exactly once."""
    events = _latest_inbound_events(db, since_hours=since_hours)
    updated = 0
    by_intent: Counter[str] = Counter()
    seen = set()
    for event in events:
        key = (event.id, event.event_type)
        if key in seen or not event.lead_id:
            continue
        seen.add(key)
        lead = db.query(B2BLead).filter(B2BLead.id == event.lead_id).first()
        if not lead:
            continue
        profile = classify_lead(db, lead)
        intent = infer_intent(event)
        if intent == "NONE":
            continue

        profile.last_intent = intent
        profile.intent = intent
        profile.last_channel = (event.channel or "").lower() or profile.last_channel
        if intent in NEGATIVE_INTENTS:
            profile.warmth = "COOLDOWN"
            lead.contact_status = "OPTED_OUT" if intent == "OPTED_OUT" else "DO_NOT_CONTACT"
            lead.contact_status_reason = f"inbound intent: {intent}"
        elif intent in ("PRICING_REQUESTED", "NEGOTIATION"):
            profile.warmth = "HOT"
        elif intent in POSITIVE_INTENTS:
            profile.warmth = "WARM"
        profile.next_action = evaluate_next_action(db, lead, profile).get("action")
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
    replies = (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.lead_id.in_(lead_ids) if lead_ids else False)
        .filter(WorkflowEvent.event_type.in_(("EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY")))
        .count()
    ) if lead_ids else 0
    sent = sum(1 for t in touches if t.status in ("SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ"))
    return {"sent": sent, "replies": replies, "reply_rate": (replies / sent * 100.0) if sent else 0.0}


def learn_from_lifecycle(db: Session, min_sample: int = 5) -> dict:
    """Persist measured category/channel reply rates; thin samples stay inert."""
    profiles = db.query(OutreachProfile).all()
    categories = sorted({p.category for p in profiles if p.category})
    channels = ("email", "whatsapp")
    learned = 0
    for category in categories:
        for channel in channels:
            m = _metric_row(db, category, channel)
            if m["sent"] < min_sample:
                continue
            row = (
                db.query(LearnedPattern)
                .filter(LearnedPattern.scope == "category_channel")
                .filter(LearnedPattern.key == f"{category}:{channel}")
                .filter(LearnedPattern.metric == "reply_rate_pct")
                .first()
            )
            if row is None:
                row = LearnedPattern(
                    scope="category_channel",
                    key=f"{category}:{channel}",
                    metric="reply_rate_pct",
                )
                db.add(row)
            row.value = round(m["reply_rate"], 2)
            row.sample_size = m["sent"]
            row.wins = m["replies"]
            row.updated_at = datetime.utcnow()
            learned += 1
    if learned:
        db.commit()
    return {"patterns_updated": learned}


def _best_channel(db: Session, category: str, eligible: tuple[str, ...] = ("email", "whatsapp")) -> str:
    """Choose the historically better eligible channel, with email as the cold default."""
    candidates = []
    for channel in eligible:
        row = (
            db.query(LearnedPattern)
            .filter(LearnedPattern.scope == "category_channel")
            .filter(LearnedPattern.key == f"{category}:{channel}")
            .filter(LearnedPattern.metric == "reply_rate_pct")
            .first()
        )
        if row and row.sample_size >= 5:
            candidates.append((float(row.value), channel))
    return max(candidates, default=(0.0, "email"))[1]


def run_automatic_cycle(db: Session, limit: int = 20) -> dict:
    """Sync memory, execute due outreach, then relearn from outcomes."""
    memory = sync_inbound_memory(db)

    leads = (
        db.query(B2BLead)
        .filter(B2BLead.contact_status.notin_(("OPTED_OUT", "DO_NOT_CONTACT", "BOUNCED")))
        .order_by(B2BLead.score.desc(), B2BLead.id.asc())
        .limit(limit)
        .all()
    )
    results = []
    for lead in leads:
        try:
            profile = classify_lead(db, lead)
            # Historical learning influences only channel selection when the
            # channel is legally/provider eligible. It never overrides
            # evaluate_next_action() or the provider gates.
            decision = evaluate_next_action(db, lead, profile)
            if decision.get("execute") and decision.get("action") in ("WARM_FIRST_TOUCH", "WARM_FOLLOW_UP"):
                preferred = _best_channel(db, profile.category)
                if preferred == "whatsapp":
                    from app.services.whatsapp_sender import consent_check
                    allowed, _ = consent_check(lead)
                    if not allowed:
                        preferred = "email"
                profile.preferred_channel = preferred
                db.flush()
            results.append({"lead_id": lead.id, "company": lead.company, **execute_one(db, lead)})
        except Exception as exc:
            db.rollback()
            results.append({"lead_id": lead.id, "company": lead.company, "status": "FAILED", "error": str(exc)[:300]})

    learning = learn_from_lifecycle(db)
    return {"memory": memory, "processed": len(results), "results": results, "learning": learning}
