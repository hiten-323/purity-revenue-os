"""Adaptive B2B outreach for Purity Beans.

One authority owns the next step: evaluate_next_action().  Classification is
an evidence/memory layer, not a competing director.  The engine learns from
lead fields and WorkflowEvent history, renders category-specific copy, and
executes only channels that have a valid provider/compliance path.

Cold leads are warmed by email automatically. WhatsApp is never used for a
cold business-initiated first touch unless a recorded consent/template path
allows it. Provider absence is recorded as NOT_CONFIGURED rather than faked as
sent. Every attempt is persisted in OutreachTouch.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Session

from app.database.database import Base
from app.models.models import B2BLead


class OutreachProfile(Base):
    __tablename__ = "outreach_profiles"
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), unique=True, nullable=False, index=True)
    category = Column(String, default="UNKNOWN", nullable=False)
    category_confidence = Column(Integer, default=0, nullable=False)
    category_evidence = Column(JSON, default=list)
    buying_angle = Column(String, default="DISCOVERY")
    warmth = Column(String, default="COLD")
    intent = Column(String, default="NONE")
    last_intent = Column(String, nullable=True)
    next_action = Column(String, nullable=True)
    next_action_at = Column(DateTime, nullable=True)
    preferred_channel = Column(String, nullable=True)
    last_channel = Column(String, nullable=True)
    touch_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OutreachTouch(Base):
    __tablename__ = "outreach_touches"
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)
    profile_id = Column(Integer, ForeignKey("outreach_profiles.id"), nullable=True)
    channel = Column(String, nullable=False)
    touch_type = Column(String, nullable=False)
    template_key = Column(String, nullable=True)
    intent = Column(String, default="NONE")
    status = Column(String, nullable=False)  # SENT/PROVIDER_ACCEPTED/BLOCKED/HELD/FAILED/NOT_CONFIGURED/SKIPPED
    provider_message_id = Column(String, nullable=True)
    payload = Column(JSON, default=dict)
    occurred_at = Column(DateTime, default=datetime.utcnow, index=True)


CATEGORY_RULES = {
    "DISTRIBUTOR": ("DISTRIBUTION", "MARGIN_AND_RANGE"),
    "WHOLESALER": ("WHOLESALE", "VOLUME_AND_MARGIN"),
    "RETAILER": ("RETAIL", "SHELF_AND_MARGIN"),
    "HORECA": ("HORECA", "CONSISTENCY_AND_SUPPLY"),
    "CORPORATE": ("CORPORATE", "PANTRY_AND_RECURRING"),
    "HOTEL": ("HOTEL", "SUPPLY_AND_CONSISTENCY"),
    "PROCUREMENT": ("PROCUREMENT", "COMMERCIAL_AND_DOCUMENTS"),
    "GIFTING": ("GIFTING", "CUSTOMIZATION_AND_BULK"),
    "PRIVATE_LABEL": ("PRIVATE_LABEL", "MOQ_AND_CUSTOMIZATION"),
    "UNKNOWN": ("DISCOVERY", "DISCOVERY"),
}


def _text(*values: Any) -> str:
    return " ".join(str(v or "") for v in values).lower()


def _history(db: Session, lead_id: int) -> list[Any]:
    try:
        from app.models.models import WorkflowEvent
        return list(
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.lead_id == lead_id)
            .order_by(WorkflowEvent.occurred_at.desc())
            .limit(50)
            .all()
        )
    except Exception:
        return []


def classify_lead(db: Session, lead: B2BLead) -> OutreachProfile:
    """Classify from current evidence + recent interaction history.

    Explicit observed signals outrank seed/search labels. A reply, pricing
    request, catalogue request, meeting or proposal history can also move the
    buying angle/warmth without rewriting the underlying business category.
    """
    evidence: list[str] = []
    hay = _text(
        lead.division, lead.industry, lead.contact_title, lead.contact_persona,
        lead.maps_types, lead.coffee_buying_evidence, lead.searched_category,
        lead.company, lead.qualification_notes, lead.current_supplier,
    )
    history = _history(db, lead.id)
    event_text = _text(*[(getattr(e, "event_type", ""), getattr(e, "channel", ""), getattr(e, "payload", "")) for e in history])

    category = "UNKNOWN"
    ordered = [
        ("PRIVATE_LABEL", ("private label", "private_label", "own brand", "contract manufacturing")),
        ("DISTRIBUTOR", ("distributor", "distribution", "stockist", "dealership")),
        ("WHOLESALER", ("wholesale", "wholesaler", "bulk trader", "cash and carry")),
        ("GIFTING", ("corporate gifting", "gift hamper", "gifting")),
        ("PROCUREMENT", ("procurement", "purchase manager", "buyer", "purchasing", "vendor management")),
        ("HOTEL", ("hotel", "resort", "hospitality")),
        ("HORECA", ("cafe", "coffee shop", "restaurant", "horeca", "canteen", "food service")),
        ("CORPORATE", ("office", "corporate", "it company", "manufacturing", "hospital", "school", "college", "facility")),
        ("RETAILER", ("retail", "supermarket", "grocery", "kirana", "mart", "store")),
    ]
    for candidate, tokens in ordered:
        hits = [t for t in tokens if t in hay]
        if hits:
            category = candidate
            evidence.extend([f"signal:{x}" for x in hits[:4]])
            break

    if category == "UNKNOWN" and lead.division:
        d = (lead.division or "").lower()
        aliases = {
            "distributor": "DISTRIBUTOR", "wholesale": "WHOLESALER", "wholesaler": "WHOLESALER",
            "retail": "RETAILER", "gifting": "GIFTING", "horeca": "HORECA", "corporate": "CORPORATE",
            "hotel": "HOTEL", "government": "PROCUREMENT", "private_label": "PRIVATE_LABEL",
        }
        category = aliases.get(d, "UNKNOWN")
        if category != "UNKNOWN": evidence.append(f"division:{d}")

    if "PRICING_REQUESTED" in event_text or "NEGOTIATION" in event_text:
        evidence.append("history:pricing_or_negotiation")
        warmth = "HOT"
        intent = "PRICING_REQUESTED"
    elif "CATALOGUE_REQUESTED" in event_text or "SAMPLE_REQUESTED" in event_text:
        evidence.append("history:catalogue_or_sample")
        warmth = "WARM"
        intent = "CATALOGUE_REQUESTED"
    elif any(x in event_text for x in ("REPLIED", "WHATSAPP_REPLY", "EMAIL_REPLIED", "MEETING_BOOKED")):
        evidence.append("history:reply")
        warmth = "ENGAGED"
        intent = "INTERESTED"
    elif any(x in event_text for x in ("EMAIL_SENT", "WHATSAPP_SENT", "OUTREACH_SENT")):
        warmth = "CONTACTED"
        intent = "NONE"
    else:
        warmth = "COLD"
        intent = "NONE"

    # Existing lifecycle state is stronger than a missing event in old data.
    if lead.status in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON"):
        warmth = "ENGAGED"
        intent = "INTERESTED" if intent == "NONE" else intent
        evidence.append(f"status:{lead.status}")

    category_name, angle = CATEGORY_RULES[category]
    confidence = min(95, 45 + min(40, len(evidence) * 10))
    if category == "UNKNOWN":
        confidence = 25

    profile = db.query(OutreachProfile).filter(OutreachProfile.lead_id == lead.id).first()
    if profile is None:
        profile = OutreachProfile(lead_id=lead.id)
        db.add(profile)
    profile.category = category
    profile.category_confidence = confidence
    profile.category_evidence = evidence
    profile.buying_angle = angle
    profile.warmth = warmth
    profile.last_intent = intent
    profile.intent = intent
    profile.updated_at = datetime.utcnow()
    db.flush()
    return profile


def _name(lead: B2BLead) -> str:
    return (lead.contact_name or "").strip().split()[0] if lead.contact_name else "there"


def render_email(lead: B2BLead, profile: OutreachProfile, touch_number: int = 1) -> tuple[str, str]:
    n = _name(lead)
    company = lead.company or "your company"
    city = lead.city or "your market"
    cat = profile.category
    if touch_number > 1:
        subject = f"Following up — Purity Beans for {company}"
        body = (
            f"Hi {n},\n\nJust following up on my note about Purity Beans. "
            f"We are speaking with selected {cat.lower()} businesses in {city}. "
            "If coffee sourcing is relevant, I can send the range and commercial details.\n\n"
            "Would you like me to send them?"
        )
        return subject, body

    drafts = {
        "DISTRIBUTOR": ("Distribution partnership — Purity Beans", f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re introducing Purity Beans premium instant coffee and are speaking with selected distribution partners in {city}.\n\nWould it be useful if I sent the catalogue and distributor commercial details?"),
        "WHOLESALER": ("Wholesale coffee range — Purity Beans", f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We supply Purity Beans premium instant coffee for wholesale and repeat B2B supply.\n\nWould you like the catalogue and wholesale rate structure?"),
        "RETAILER": ("Purity Beans — retail coffee partnership", f"Hi {n},\n\nWe’re introducing Purity Beans premium instant coffee for selected retail partners in {city}. The proposition is designed around shelf-ready packs and healthy retailer economics.\n\nMay I send the catalogue and trade details?"),
        "HORECA": ("Coffee supply for {company}", f"Hi {n},\n\nI came across {company} and thought Purity Beans could be relevant for your coffee requirements. We supply premium instant coffee with consistent product and B2B supply support.\n\nWould you like me to send the range for evaluation?"),
        "HOTEL": ("Purity Beans — hotel coffee supply", f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re onboarding selected hospitality partners for Purity Beans.\n\nIf coffee procurement is relevant at {company}, may I send the range and commercial information?"),
        "CORPORATE": ("Office coffee option — Purity Beans", f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re introducing Purity Beans as a convenient premium coffee option for office/pantry requirements.\n\nIf you handle pantry or procurement, may I send the range and commercial details?"),
        "PROCUREMENT": ("B2B coffee supply — Purity Beans", f"Hi {n},\n\nI’m reaching out from Pure Pantry Provisions regarding B2B supply of Purity Beans instant coffee. We can share product, pack-size, commercial and supply information for evaluation.\n\nAre you the right person for coffee/pantry procurement at {company}?"),
        "GIFTING": ("Corporate coffee gifting — Purity Beans", f"Hi {n},\n\nWe’re developing premium Purity Beans coffee gifting options for corporate requirements, including bulk and customised programmes.\n\nWould you like me to send the catalogue?"),
        "PRIVATE_LABEL": ("Private-label coffee — Pure Pantry Provisions", f"Hi {n},\n\nWe support private-label coffee programmes with custom packaging and B2B supply. If {company} is evaluating a coffee line, I can share our capabilities and MOQ details.\n\nWould you like the information?"),
        "UNKNOWN": ("A quick coffee supply question", f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re introducing Purity Beans, a premium instant coffee range, and I wanted to check whether coffee sourcing is relevant at {company}.\n\nIf yes, may I send a short catalogue?"),
    }
    return drafts.get(cat, drafts["UNKNOWN"])


def _wa_text(lead: B2BLead, profile: OutreachProfile) -> str:
    n = _name(lead)
    return f"Hi {n}, Hiten here from Pure Pantry Provisions. We’re introducing Purity Beans and thought it may be relevant for {lead.company or 'your business'}. Would you like me to send the catalogue?"


def _negative(history: list[Any]) -> bool:
    text = _text(*[(getattr(e, "event_type", ""), getattr(e, "payload", "")) for e in history])
    return any(x in text for x in ("UNSUBSCRIBED", "DO_NOT_CONTACT", "NOT_INTERESTED", "OPTED_OUT", "COMPLAINT"))


def evaluate_next_action(db: Session, lead: B2BLead, profile: OutreachProfile | None = None) -> dict:
    """Single next-action authority for adaptive outreach."""
    profile = profile or classify_lead(db, lead)
    history = _history(db, lead.id)
    if _negative(history) or getattr(lead, "contact_status", "") in ("OPTED_OUT", "DO_NOT_CONTACT"):
        return {"action": "COOLDOWN", "channel": None, "reason": "negative/opt-out signal", "execute": False}
    if profile.intent in ("PRICING_REQUESTED", "NEGOTIATION"):
        return {"action": "FOUNDER_REVIEW", "channel": "email", "reason": "commercial negotiation requires pricing policy", "execute": False}
    if profile.intent == "CATALOGUE_REQUESTED":
        return {"action": "SEND_CATALOGUE", "channel": "whatsapp" if (lead.consent_status or "UNKNOWN") in ("EXPLICIT", "OPTED_IN", "IMPLIED_B2B") else "email", "reason": "prospect explicitly requested catalogue", "execute": True}
    if profile.warmth in ("ENGAGED", "WARM", "HOT"):
        return {"action": "CONTINUE_CONVERSATION", "channel": profile.last_channel or "email", "reason": "response/intent history exists", "execute": True}

    # Cold leads: email is the default automatic warming channel.
    sent = sum(1 for t in history if getattr(t, "event_type", "") in ("EMAIL_SENT", "OUTREACH_EMAIL_SENT"))
    if sent == 0:
        return {"action": "WARM_FIRST_TOUCH", "channel": "email", "reason": "cold lead with no proven email touch", "execute": True}
    if sent == 1:
        last = next((t for t in history if getattr(t, "event_type", "") in ("EMAIL_SENT", "OUTREACH_EMAIL_SENT")), None)
        if last and getattr(last, "occurred_at", None) and datetime.utcnow() - last.occurred_at >= timedelta(days=3):
            return {"action": "WARM_FOLLOW_UP", "channel": "email", "reason": "no response after initial touch", "execute": True}
    return {"action": "NURTURE", "channel": None, "reason": "cadence not yet due", "execute": False}


def _record(db: Session, lead: B2BLead, profile: OutreachProfile, channel: str, action: str, status: str, template: str, **payload: Any) -> OutreachTouch:
    touch = OutreachTouch(lead_id=lead.id, profile_id=profile.id, channel=channel, touch_type=action, template_key=template, status=status, payload=payload)
    db.add(touch)
    profile.touch_count = (profile.touch_count or 0) + 1
    profile.last_channel = channel
    profile.next_action = action
    profile.updated_at = datetime.utcnow()
    db.flush()
    return touch


def execute_one(db: Session, lead: B2BLead) -> dict:
    profile = classify_lead(db, lead)
    decision = evaluate_next_action(db, lead, profile)
    if not decision["execute"]:
        return {**decision, "status": "SKIPPED"}

    if decision["channel"] == "email":
        from app.services.email_sender import build_outreach_email, send_email
        count = profile.touch_count or 0
        subject, body = render_email(lead, profile, 1 if count == 0 else 2)
        email = build_outreach_email(lead.email or "", lead.contact_name or "", lead.company or "", subject, body, lead_id=lead.id)
        if not lead.email:
            _record(db, lead, profile, "email", decision["action"], "BLOCKED", "missing_email")
            db.commit()
            return {**decision, "status": "BLOCKED", "reason": "missing email"}
        result = send_email(email)
        status = "SENT" if result.status == "sent" else result.status.upper()
        _record(db, lead, profile, "email", decision["action"], status, "typed_day0" if count == 0 else "typed_followup", error=result.error, message_id=result.message_id)
        db.commit()
        return {**decision, "status": status, "error": result.error, "message_id": result.message_id}

    if decision["channel"] == "whatsapp":
        from app.services.whatsapp_sender import send_whatsapp
        result = send_whatsapp(lead, _wa_text(lead, profile))
        _record(db, lead, profile, "whatsapp", decision["action"], result.status.upper(), "catalogue_request", reason=result.reason, message_id=result.message_id)
        db.commit()
        return {**decision, "status": result.status.upper(), "reason": result.reason, "message_id": result.message_id}

    _record(db, lead, profile, decision["channel"] or "unknown", decision["action"], "NOT_CONFIGURED", "none", reason="provider not configured")
    db.commit()
    return {**decision, "status": "NOT_CONFIGURED"}


def run_cycle(db: Session, limit: int = 20) -> dict:
    """Classify and execute due outreach without founder approval.

    Never invents a send: every provider result is persisted and every blocked
    or unconfigured path is explicit. Negative/opted-out contacts stop.
    """
    leads = (
        db.query(B2BLead)
        .filter(B2BLead.contact_status.notin_(["OPTED_OUT", "DO_NOT_CONTACT", "BOUNCED"]))
        .order_by(B2BLead.score.desc(), B2BLead.id.asc())
        .limit(limit)
        .all()
    )
    results = []
    for lead in leads:
        try:
            results.append({"lead_id": lead.id, "company": lead.company, **execute_one(db, lead)})
        except Exception as exc:
            db.rollback()
            try:
                profile = classify_lead(db, lead)
                _record(db, lead, profile, "system", "OUTREACH_ERROR", "FAILED", "none", error=str(exc)[:300])
                db.commit()
            except Exception:
                db.rollback()
            results.append({"lead_id": lead.id, "company": lead.company, "status": "FAILED", "error": str(exc)[:300]})
    return {"processed": len(results), "results": results}
