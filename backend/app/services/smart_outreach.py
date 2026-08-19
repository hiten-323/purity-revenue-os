"""Adaptive B2B outreach for Purity Beans.

One authority owns the next step: evaluate_next_action().  Classification is
an evidence/memory layer, not a competing director.  The engine learns from
lead fields and WorkflowEvent history, renders category-specific copy, and
executes only channels that have a valid provider/compliance path.

Cold leads are warmed by email automatically. WhatsApp is never used for a
cold business-initiated first touch unless a recorded consent/template path
allows it. Provider absence is recorded as NOT_CONFIGURED rather than faked as
sent. Every attempt is persisted in OutreachTouch.

Cadence is driven by OutreachTouch proof so worker cycles cannot spam.
Engaged conversations are not re-blasted; CONTINUE_CONVERSATION does not send.
"""
from __future__ import annotations

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

PROVEN_SEND = ("SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ")
WARM_ACTIONS = ("WARM_FIRST_TOUCH", "WARM_FOLLOW_UP", "SEND_CATALOGUE")
FOLLOW_UP_AFTER_DAYS = 3


def _text(*values: Any) -> str:
    return " ".join(str(v or "") for v in values).lower()


def _proven_sends(db: Session, lead_id: int) -> list[Any]:
    """
    Every PROVEN outbound email for this lead, newest first, from both ledgers.

    Returns objects exposing .occurred_at. WorkflowEvent EMAIL_SENT is
    authoritative (proof-enforced at insert); OutreachTouch rows are included
    so cadence still works for channels that only write there.
    """
    from app.models.models import WorkflowEvent

    events = (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.lead_id == lead_id,
                WorkflowEvent.event_type == "EMAIL_SENT")
        .all()
    )
    touches = _proven_email_touches(db, lead_id)
    both = list(events) + list(touches)
    return sorted(both,
                  key=lambda r: getattr(r, "occurred_at", None) or datetime.min,
                  reverse=True)


def _mentions(haystack: str, *needles: str) -> bool:
    """
    Case-insensitive membership.

    _text() lowercases what it builds, while every workflow event_type is
    written UPPERCASE, so `"CATALOGUE_REQUESTED" in event_text` could never be
    True. Warmth derived from history was therefore dead: a lead that had asked
    for a catalogue, asked for pricing, or replied still classified as COLD,
    and every warm follow-up path was unreachable in production.
    """
    return any(n.lower() in haystack for n in needles)


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
    except Exception as e:
        # Returning [] here reads downstream as "this lead has no history",
        # which classifies a warm buyer as COLD and silently suppresses the
        # warm follow-up they were owed. That is how the case-mismatch above
        # went unnoticed: the symptom (everyone COLD) had two possible causes
        # and neither said anything. A history we could not read is not a
        # history that is empty.
        print(f"[smart_outreach] history unreadable for lead {lead_id}: "
              f"{e.__class__.__name__} — treating as UNKNOWN, not cold")
        raise


def _proven_email_touches(db: Session, lead_id: int) -> list[OutreachTouch]:
    """Cadence source of truth — worker cycles must not re-send Day-0."""
    return (
        db.query(OutreachTouch)
        .filter(
            OutreachTouch.lead_id == lead_id,
            OutreachTouch.channel == "email",
            OutreachTouch.status.in_(PROVEN_SEND),
            OutreachTouch.touch_type.in_(WARM_ACTIONS),
        )
        .order_by(OutreachTouch.occurred_at.desc())
        .all()
    )


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
    event_text = _text(*[
        (getattr(e, "event_type", ""), getattr(e, "channel", ""), getattr(e, "payload", ""))
        for e in history
    ])

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
        if category != "UNKNOWN":
            evidence.append(f"division:{d}")

    # Prefer durable profile intent set by inbound memory when present.
    profile = db.query(OutreachProfile).filter(OutreachProfile.lead_id == lead.id).first()
    prior_intent = (profile.intent if profile else None) or "NONE"

    if prior_intent in ("OPTED_OUT", "NOT_INTERESTED", "DO_NOT_CONTACT", "COMPLAINT"):
        warmth, intent = "COOLDOWN", prior_intent
        evidence.append(f"memory:{prior_intent}")
    elif prior_intent in ("PRICING_REQUESTED", "NEGOTIATION") or _mentions(event_text, "PRICING_REQUESTED", "NEGOTIATION"):
        evidence.append("history:pricing_or_negotiation")
        warmth, intent = "HOT", "PRICING_REQUESTED"
    elif prior_intent == "CATALOGUE_REQUESTED" or _mentions(event_text, "CATALOGUE_REQUESTED", "SAMPLE_REQUESTED"):
        evidence.append("history:catalogue_or_sample")
        warmth, intent = "WARM", "CATALOGUE_REQUESTED"
    elif prior_intent in ("INTERESTED", "CALLBACK", "MEETING_REQUESTED") or _mentions(event_text, "REPLIED", "WHATSAPP_REPLY", "EMAIL_REPLIED", "MEETING_BOOKED", "EMAIL_REPLY"):
        evidence.append("history:reply")
        warmth, intent = "ENGAGED", prior_intent if prior_intent != "NONE" else "INTERESTED"
    elif _mentions(event_text, "EMAIL_SENT", "WHATSAPP_SENT", "OUTREACH_SENT") or _proven_email_touches(db, lead.id):
        warmth, intent = "CONTACTED", "NONE"
    else:
        warmth, intent = "COLD", "NONE"

    if lead.status in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON"):
        if warmth not in ("COOLDOWN", "HOT"):
            warmth = "ENGAGED"
            intent = "INTERESTED" if intent == "NONE" else intent
            evidence.append(f"status:{lead.status}")

    _, angle = CATEGORY_RULES[category]
    confidence = min(95, 45 + min(40, len(evidence) * 10))
    if category == "UNKNOWN":
        confidence = 25

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
        "DISTRIBUTOR": (
            "Distribution partnership — Purity Beans",
            f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re introducing Purity Beans premium instant coffee and are speaking with selected distribution partners in {city}.\n\nWould it be useful if I sent the catalogue and distributor commercial details?",
        ),
        "WHOLESALER": (
            "Wholesale coffee range — Purity Beans",
            f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We supply Purity Beans premium instant coffee for wholesale and repeat B2B supply.\n\nWould you like the catalogue and wholesale rate structure?",
        ),
        "RETAILER": (
            "Purity Beans — retail coffee partnership",
            f"Hi {n},\n\nWe’re introducing Purity Beans premium instant coffee for selected retail partners in {city}. The proposition is designed around shelf-ready packs and healthy retailer economics.\n\nMay I send the catalogue and trade details?",
        ),
        "HORECA": (
            f"Coffee supply for {company}",
            f"Hi {n},\n\nI came across {company} and thought Purity Beans could be relevant for your coffee requirements. We supply premium instant coffee with consistent product and B2B supply support.\n\nWould you like me to send the range for evaluation?",
        ),
        "HOTEL": (
            "Purity Beans — hotel coffee supply",
            f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re onboarding selected hospitality partners for Purity Beans.\n\nIf coffee procurement is relevant at {company}, may I send the range and commercial information?",
        ),
        "CORPORATE": (
            "Office coffee option — Purity Beans",
            f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re introducing Purity Beans as a convenient premium coffee option for office/pantry requirements.\n\nIf you handle pantry or procurement, may I send the range and commercial details?",
        ),
        "PROCUREMENT": (
            "B2B coffee supply — Purity Beans",
            f"Hi {n},\n\nI’m reaching out from Pure Pantry Provisions regarding B2B supply of Purity Beans instant coffee. We can share product, pack-size, commercial and supply information for evaluation.\n\nAre you the right person for coffee/pantry procurement at {company}?",
        ),
        "GIFTING": (
            "Corporate coffee gifting — Purity Beans",
            f"Hi {n},\n\nWe’re developing premium Purity Beans coffee gifting options for corporate requirements, including bulk and customised programmes.\n\nWould you like me to send the catalogue?",
        ),
        "PRIVATE_LABEL": (
            "Private-label coffee — Pure Pantry Provisions",
            f"Hi {n},\n\nWe support private-label coffee programmes with custom packaging and B2B supply. If {company} is evaluating a coffee line, I can share our capabilities and MOQ details.\n\nWould you like the information?",
        ),
        "UNKNOWN": (
            "A quick coffee supply question",
            f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We’re introducing Purity Beans, a premium instant coffee range, and I wanted to check whether coffee sourcing is relevant at {company}.\n\nIf yes, may I send a short catalogue?",
        ),
    }
    return drafts.get(cat, drafts["UNKNOWN"])


def _wa_text(lead: B2BLead, profile: OutreachProfile) -> str:
    n = _name(lead)
    return (
        f"Hi {n}, Hiten here from Pure Pantry Provisions. We’re introducing Purity Beans "
        f"and thought it may be relevant for {lead.company or 'your business'}. "
        "Would you like me to send the catalogue?"
    )


# Kept identical to decision_engine's suppression set on purpose. Two modules
# disagreeing about what "stop contacting them" means is how a buyer who
# unsubscribed keeps receiving automated outreach.
SUPPRESSION_EVENTS = ("UNSUBSCRIBED", "DO_NOT_CONTACT", "NOT_INTERESTED",
                      "OPTED_OUT", "COMPLAINT", "remove me")


def _negative(history: list[Any]) -> bool:
    """
    Has this contact told us to stop?

    This compared UPPERCASE needles against text _text() had already
    lowercased, so nothing ever matched and UNSUBSCRIBED did not stop
    automation — an opt-out was recorded and then ignored. decision_engine
    honoured the same events correctly, which is precisely the damage two
    decision authorities do.
    """
    text = _text(*[(getattr(e, "event_type", ""), getattr(e, "payload", "")) for e in history])
    return _mentions(text, *SUPPRESSION_EVENTS)


def evaluate_next_action(db: Session, lead: B2BLead, profile: OutreachProfile | None = None) -> dict:
    """
    Chooses the SHAPE of an adaptive touch — which message, on which channel.

    It is not a second gate. decision_engine.evaluate_next_action remains the
    authority on whether this lead may be contacted at all (suppression, trust,
    account frequency, sequence position, delivery guard), and this function
    refuses whenever that one refuses.

    Both modules used to answer "may we contact them?" independently, and they
    disagreed: this one compared UPPERCASE event names against lowercased text,
    so UNSUBSCRIBED never matched and an opt-out did not stop automation, while
    decision_engine honoured the same event correctly. Whichever ran last won.
    """
    profile = profile or classify_lead(db, lead)
    history = _history(db, lead.id)

    # Local opt-out read stays as a cheap first pass, but it is no longer the
    # only thing standing between an unsubscribe and an automated send.
    if _negative(history) or getattr(lead, "contact_status", "") in ("OPTED_OUT", "DO_NOT_CONTACT") or profile.intent in (
        "OPTED_OUT", "NOT_INTERESTED", "DO_NOT_CONTACT", "COMPLAINT"
    ):
        return {"action": "COOLDOWN", "channel": None, "reason": "negative/opt-out signal", "execute": False}

    # The single authority. Consulted for permission only — its SEND verdict
    # does not dictate which message goes out, which is what this module knows
    # and it does not.
    try:
        from app.services.decision_engine import evaluate_next_action as _authority
        verdict = _authority(lead, db)
    except Exception as e:
        # A gate that cannot answer must never read as permission.
        return {"action": "FOUNDER_REVIEW", "channel": None, "execute": False,
                "reason": f"decision engine unavailable ({e.__class__.__name__}) "
                          f"— refusing to send without it"}

    if verdict["action"] == "SUPPRESS":
        return {"action": "COOLDOWN", "channel": None, "execute": False,
                "reason": f"decision engine: {verdict['reason']}"}
    if verdict["action"] in ("WAIT", "NONE", "ENRICH"):
        return {"action": "NURTURE", "channel": None, "execute": False,
                "reason": f"decision engine: {verdict['reason']}"}
    if verdict["action"] == "FOUNDER_REVIEW":
        return {"action": "FOUNDER_REVIEW", "channel": None, "execute": False,
                "reason": f"decision engine: {verdict['reason']}"}

    if profile.intent in ("PRICING_REQUESTED", "NEGOTIATION"):
        return {
            "action": "FOUNDER_REVIEW",
            "channel": "email",
            "reason": "commercial negotiation requires pricing policy",
            "execute": False,
        }

    if profile.intent == "CATALOGUE_REQUESTED":
        # Prefer WA only with recorded consent; else email catalogue.
        consent = (getattr(lead, "consent_status", None) or "UNKNOWN").upper()
        channel = "whatsapp" if consent in ("EXPLICIT", "OPTED_IN", "IMPLIED_B2B") else "email"
        return {
            "action": "SEND_CATALOGUE",
            "channel": channel,
            "reason": "prospect explicitly requested catalogue",
            "execute": True,
        }

    # Engaged = wait for human/intent path; do not auto-blast another warm email.
    if profile.warmth in ("ENGAGED", "WARM", "HOT") and profile.intent not in ("CATALOGUE_REQUESTED",):
        return {
            "action": "CONTINUE_CONVERSATION",
            "channel": profile.last_channel or "email",
            "reason": "response/intent history exists — no automatic re-send",
            "execute": False,
        }

    # Cadence from the system's single send ledger.
    #
    # This counted OutreachTouch rows only, so a send recorded the way the rest
    # of the system records one — a WorkflowEvent EMAIL_SENT — was invisible
    # here and the lead looked never-contacted. Two ledgers of "did we send"
    # is the same duplication that put a second evaluate_next_action in this
    # module. EMAIL_SENT is already the trustworthy one: the strict send-proof
    # listener renames anything lacking a recipient and provider message-id to
    # EMAIL_SENT_UNPROVEN, so counting EMAIL_SENT counts only real sends.
    proven = _proven_sends(db, lead.id)
    sent = len(proven)
    if sent == 0:
        return {
            "action": "WARM_FIRST_TOUCH",
            "channel": "email",
            "reason": "cold lead with no proven email touch",
            "execute": True,
        }
    if sent == 1:
        last = proven[0]
        if last.occurred_at and datetime.utcnow() - last.occurred_at >= timedelta(days=FOLLOW_UP_AFTER_DAYS):
            return {
                "action": "WARM_FOLLOW_UP",
                "channel": "email",
                "reason": "no response after initial touch",
                "execute": True,
            }
        return {
            "action": "NURTURE",
            "channel": None,
            "reason": f"awaiting {FOLLOW_UP_AFTER_DAYS}d cadence after first touch",
            "execute": False,
        }
    return {"action": "NURTURE", "channel": None, "reason": "cadence complete or not due", "execute": False}


def _record(
    db: Session,
    lead: B2BLead,
    profile: OutreachProfile,
    channel: str,
    action: str,
    status: str,
    template: str,
    **payload: Any,
) -> OutreachTouch:
    touch = OutreachTouch(
        lead_id=lead.id,
        profile_id=profile.id,
        channel=channel,
        touch_type=action,
        template_key=template,
        status=status,
        provider_message_id=payload.get("message_id") or payload.get("provider_message_id"),
        payload=payload,
    )
    db.add(touch)
    if status in PROVEN_SEND:
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

    # Hard duplicate guard: never two proven first touches.
    if decision["action"] == "WARM_FIRST_TOUCH" and _proven_email_touches(db, lead.id):
        return {**decision, "status": "SKIPPED", "reason": "duplicate first touch blocked"}

    if decision["channel"] == "email":
        from app.services.email_sender import build_outreach_email, send_email

        proven_n = len(_proven_email_touches(db, lead.id))
        subject, body = render_email(lead, profile, 1 if proven_n == 0 else 2)
        if decision["action"] == "SEND_CATALOGUE":
            import os

            url = (os.getenv("PURITY_BEANS_CATALOGUE_URL") or "").strip()
            if not url:
                _record(db, lead, profile, "email", "SEND_CATALOGUE", "NOT_CONFIGURED", "catalogue_email", reason="PURITY_BEANS_CATALOGUE_URL missing")
                db.commit()
                return {**decision, "status": "NOT_CONFIGURED", "reason": "PURITY_BEANS_CATALOGUE_URL is not configured"}
            subject = f"Purity Beans catalogue — {lead.company or 'your request'}"
            body = (
                f"Hi {_name(lead)},\n\nAs requested, here is our catalogue:\n{url}\n\n"
                "Happy to share commercial details next if useful.\n\nBest,\nHiten\nPure Pantry Provisions"
            )
        if not lead.email:
            _record(db, lead, profile, "email", decision["action"], "BLOCKED", "missing_email")
            db.commit()
            return {**decision, "status": "BLOCKED", "reason": "missing email"}
        email = build_outreach_email(
            lead.email or "",
            lead.contact_name or "",
            lead.company or "",
            subject,
            body,
            lead_id=lead.id,
        )
        result = send_email(email)
        status = "SENT" if result.status == "sent" else result.status.upper()
        # Never claim SENT without a provider message id when status is sent
        if status == "SENT" and not getattr(result, "message_id", None):
            status = "UNPROVEN"
        _record(
            db,
            lead,
            profile,
            "email",
            decision["action"],
            status,
            "typed_day0" if proven_n == 0 and decision["action"] != "SEND_CATALOGUE" else (
                "catalogue_email" if decision["action"] == "SEND_CATALOGUE" else "typed_followup"
            ),
            error=result.error,
            message_id=result.message_id,
        )
        db.commit()
        return {**decision, "status": status, "error": result.error, "message_id": result.message_id}

    if decision["channel"] == "whatsapp":
        from app.services.whatsapp_sender import send_whatsapp

        result = send_whatsapp(lead, _wa_text(lead, profile))
        _record(
            db,
            lead,
            profile,
            "whatsapp",
            decision["action"],
            result.status.upper(),
            "catalogue_request",
            reason=result.reason,
            message_id=result.message_id,
        )
        db.commit()
        return {**decision, "status": result.status.upper(), "reason": result.reason, "message_id": result.message_id}

    _record(
        db,
        lead,
        profile,
        decision["channel"] or "unknown",
        decision["action"],
        "NOT_CONFIGURED",
        "none",
        reason="provider not configured",
    )
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
