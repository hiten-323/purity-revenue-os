"""Lifecycle memory and learning for automatic B2B outreach.

This module does not create a second decision engine. It feeds observed
interaction history into smart_outreach.evaluate_next_action(), records intent,
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
from app.services.smart_outreach import OutreachProfile, OutreachTouch, classify_lead, evaluate_next_action, execute_one

POSITIVE_INTENTS = {"CATALOGUE_REQUESTED", "PRICING_REQUESTED", "SAMPLE_REQUESTED", "MEETING_REQUESTED", "CALLBACK", "INTERESTED", "NEGOTIATION"}
NEGATIVE_INTENTS = {"NOT_INTERESTED", "OPTED_OUT", "DO_NOT_CONTACT", "COMPLAINT"}


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
    text = _text(event.event_type) + " " + _text(event.payload)
    if any(x in text for x in ("unsubscribe", "opted_out", "do_not_contact", "remove me", "not interested")):
        return "OPTED_OUT" if any(x in text for x in ("unsubscribe", "opted_out")) else "NOT_INTERESTED"
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
        .filter(WorkflowEvent.event_type.in_(["EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY", "UNSUBSCRIBED", "OPTED_OUT", "DO_NOT_CONTACT", "COMPLAINT"]))
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
    touches = db.query(OutreachTouch).join(OutreachProfile, OutreachTouch.profile_id == OutreachProfile.id).filter(OutreachProfile.category == category, OutreachTouch.channel == channel).all()
    lead_ids = {t.lead_id for t in touches}
    replies = 0
    if lead_ids:
        replies = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id.in_(lead_ids)).filter(WorkflowEvent.event_type.in_(("EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY"))).count()
    sent = sum(1 for t in touches if t.status in ("SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ"))
    return {"sent": sent, "replies": replies, "reply_rate": (replies / sent * 100.0) if sent else 0.0}


def learn_from_lifecycle(db: Session, min_sample: int = 5) -> dict:
    ensure_schema()
    categories = sorted({p.category for p in db.query(OutreachProfile).all() if p.category})
    learned = 0
    for category in categories:
        for channel in ("email", "whatsapp"):
            metric = _metric_row(db, category, channel)
            if metric["sent"] < min_sample:
                continue
            row = db.query(LearnedPattern).filter(LearnedPattern.scope == "category_channel", LearnedPattern.key == f"{category}:{channel}", LearnedPattern.metric == "reply_rate_pct").first()
            if row is None:
                row = LearnedPattern(scope="category_channel", key=f"{category}:{channel}", metric="reply_rate_pct")
                db.add(row)
            row.value = round(metric["reply_rate"], 2)
            row.sample_size = metric["sent"]
            row.wins = metric["replies"]
            row.updated_at = datetime.utcnow()
            learned += 1
    if learned:
        db.commit()
    return {"patterns_updated": learned}


def _best_channel(db: Session, category: str) -> str:
    candidates = []
    for channel in ("email", "whatsapp"):
        row = db.query(LearnedPattern).filter(LearnedPattern.scope == "category_channel", LearnedPattern.key == f"{category}:{channel}", LearnedPattern.metric == "reply_rate_pct").first()
        if row and row.sample_size >= 5:
            candidates.append((float(row.value), channel))
    return max(candidates, default=(0.0, "email"))[1]


def _send_requested_catalogue(db: Session, lead: B2BLead, profile: OutreachProfile) -> dict:
    """Fulfil an explicit catalogue request automatically; never invent a URL/template."""
    url = (os.getenv("PURITY_BEANS_CATALOGUE_URL") or "").strip()
    if not url:
        return {"status": "NOT_CONFIGURED", "reason": "PURITY_BEANS_CATALOGUE_URL is not configured"}
    from app.services.whatsapp_sender import consent_check, send_whatsapp
    allowed, reason = consent_check(lead)
    if not allowed:
        return {"status": "BLOCKED", "reason": reason}
    campaign = (os.getenv("AISENSY_CATALOGUE_CAMPAIGN_NAME") or os.getenv("AISENSY_CAMPAIGN_NAME") or "").strip()
    if not campaign:
        return {"status": "NOT_CONFIGURED", "reason": "AISENSY_CATALOGUE_CAMPAIGN_NAME/AISENSY_CAMPAIGN_NAME is not configured"}
    params = [lead.contact_name or lead.company or "there", url]
    result = send_whatsapp(lead, f"Catalogue: {url}", campaign_name=campaign, template_params=params)
    touch = OutreachTouch(lead_id=lead.id, profile_id=profile.id, channel="whatsapp", touch_type="SEND_CATALOGUE", template_key="catalogue_request", status=result.status.upper(), provider_message_id=result.message_id, payload={"catalogue_url": url, "reason": "explicit catalogue request", "provider_reason": result.reason})
    db.add(touch)
    db.commit()
    return {"status": result.status.upper(), "reason": result.reason, "message_id": result.message_id}


def run_automatic_cycle(db: Session, limit: int = 20) -> dict:
    """Sync memory, execute due outreach, fulfil explicit catalogue requests, then learn."""
    ensure_schema()
    memory = sync_inbound_memory(db)
    leads = db.query(B2BLead).filter(B2BLead.contact_status.notin_(("OPTED_OUT", "DO_NOT_CONTACT", "BOUNCED"))).order_by(B2BLead.score.desc(), B2BLead.id.asc()).limit(limit).all()
    results = []
    for lead in leads:
        try:
            profile = classify_lead(db, lead)
            decision = evaluate_next_action(db, lead, profile)
            if decision.get("action") == "SEND_CATALOGUE" and decision.get("execute"):
                result = _send_requested_catalogue(db, lead, profile)
                results.append({"lead_id": lead.id, "company": lead.company, **decision, **result})
                continue
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
