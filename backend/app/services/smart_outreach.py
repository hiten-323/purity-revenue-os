"""Adaptive B2B outreach for Purity Beans.

decision_engine.evaluate_next_action owns permission; plan_touch shapes the
touch within it.  Classification is
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
    "CAFE": ("CAFE", "BEVERAGE_AND_REPEAT"),
    "RESTAURANT": ("RESTAURANT", "KITCHEN_AND_SUPPLY"),
    "CANTEEN": ("CANTEEN", "VOLUME_AND_CONSISTENCY"),
    "HOSPITAL": ("HOSPITAL", "PANTRY_AND_VISITOR"),
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


def _contactable_first():
    """Order key: 0 for leads a channel could actually reach, 1 for the rest.

    "Could" is deliberately weak — it asks whether any channel has a standing
    permission on this row, not whether this particular touch is due. The
    authority on that is still evaluate_next_action, which runs per candidate
    immediately below; this only decides who gets looked at first, so a lead
    that ranks 0 and is then refused costs one evaluation, while a lead that
    never ranks at all costs the whole channel.

    Both vocabularies are imported: trust_promoter.MAY_SEND for email and
    whatsapp_sender.CONSENT_OK for WhatsApp. Restating either here is how the
    trust engine and the sender came to disagree about 29 addresses.
    """
    from sqlalchemy import case

    from app.services.trust_promoter import MAY_SEND
    from app.services.whatsapp_sender import CONSENT_OK

    return case(
        (B2BLead.email_trust.in_(tuple(MAY_SEND)), 0),
        (B2BLead.consent_status.in_(tuple(CONSENT_OK)), 0),
        else_=1,
    )


def _proven_touch(db: Session, lead_id: int, touch_type: str) -> bool:
    """General one-shot-touch guard, channel-agnostic. Same PROVEN_SEND
    definition as _proven_email_touches; that one stays name-specific
    because WARM_ACTIONS is its own list, not because the underlying
    question is different."""
    return (
        db.query(OutreachTouch)
        .filter(
            OutreachTouch.lead_id == lead_id,
            OutreachTouch.touch_type == touch_type,
            OutreachTouch.status.in_(PROVEN_SEND),
        )
        .first()
        is not None
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
        # Cafe / restaurant / canteen / hospital are NOT "hospitality" or "office".
        # Search intent must not collapse them into HORECA/CORPORATE.
        ("CAFE", ("cafe", "coffee shop", "coffeehouse", "espresso", "cafeteria bar")),
        ("RESTAURANT", ("restaurant", "dhaba", "eatery", "fine dining")),
        ("CANTEEN", ("canteen", "staff mess", "industrial kitchen", "factory canteen")),
        ("HOSPITAL", ("hospital", "nursing home", "multispeciality", "multi speciality")),
        ("HOTEL", ("hotel", "resort")),
        ("HORECA", ("horeca", "food service", "catering")),
        ("CORPORATE", ("office", "corporate", "it company", "manufacturing", "school", "college", "facility")),
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
            "cafe": "CAFE", "restaurant": "RESTAURANT", "hospital": "HOSPITAL",
            "canteen": "CANTEEN", "canteen_org": "CANTEEN",
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
    elif prior_intent == "BOUNCED" or _mentions(event_text, "EMAIL_BOUNCED", "HARD_BOUNCE"):
        warmth, intent = "COOLDOWN", "BOUNCED"
        evidence.append("history:bounce")
    elif prior_intent in ("OUT_OF_OFFICE", "MACHINE_REPLY", "CALL_LATER"):
        warmth, intent = "CONTACTED", prior_intent
        evidence.append(f"memory:{prior_intent}")
    elif (
        (getattr(lead, "consent_source", "") or "") == "AI_CALL_WHATSAPP_REQUEST"
        and not _proven_touch(db, lead.id, "SEND_CALL_FOLLOWUP")
    ):
        # The prospect asked for WhatsApp DURING a live call minutes or hours
        # ago — the highest-intent, freshest signal this classifier ever
        # sees, and one the generic warmth ladder below has no way to
        # recognise (it only reads reply-history text, and a phone call
        # writes no such text). Before this branch existed, this lead fell
        # through to COLD/CONTACTED and got the same cold-open message
        # ("We're introducing Purity Beans...") as a lead who has never
        # spoken to anyone here — exactly backwards for someone who just
        # finished a qualification conversation and named the channel
        # themselves. Guarded by _proven_touch so this fires exactly once:
        # after the follow-up sends, later cycles fall through to the
        # normal reply/warmth ladder like any other contacted lead.
        evidence.append("history:ai_call_whatsapp_opt_in")
        warmth, intent = "HOT", "AI_CALL_WHATSAPP_FOLLOWUP"
    elif prior_intent in ("PRICING_REQUESTED", "NEGOTIATION") or _mentions(event_text, "PRICING_REQUESTED", "NEGOTIATION"):
        evidence.append("history:pricing_or_negotiation")
        warmth, intent = "HOT", "PRICING_REQUESTED"
    elif prior_intent == "SAMPLE_REQUESTED" or _mentions(event_text, "SAMPLE_REQUESTED"):
        evidence.append("history:sample")
        warmth, intent = "WARM", "SAMPLE_REQUESTED"
    elif prior_intent == "CATALOGUE_REQUESTED" or _mentions(event_text, "CATALOGUE_REQUESTED"):
        evidence.append("history:catalogue")
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

    _, angle = CATEGORY_RULES.get(category, CATEGORY_RULES["UNKNOWN"])
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


def _audience(category: str | None) -> str:
    """Buyer-facing plural for a category; never the raw internal key."""
    from app.services.lead_quality import audience_label
    return audience_label(category)


def render_email(lead: B2BLead, profile: OutreachProfile, touch_number: int = 1,
                 touch: str | None = None) -> tuple[str, str]:
    """Category-specific copy for the cadence touch that is actually due.

    touch_number is kept for callers/tests. Prefer `touch` (intro/nudge/proof/
    ask/breakup) when the sequence engine named one — otherwise every follow-up
    was the same "just following up" paragraph.
    """
    n = _name(lead)
    company = lead.company or "your company"
    city = lead.city or "your market"
    cat = profile.category or "UNKNOWN"
    audience = _audience(cat)
    which = (touch or "").lower()
    if not which:
        which = "intro" if touch_number <= 1 else "nudge"

    intro = {
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
        "CAFE": (
            f"Coffee for {company}",
            f"Hi {n},\n\nI noticed {company} in {city} and thought Purity Beans could sit well on your café menu — consistent premium instant for rush hours without a full espresso setup.\n\nWould you like me to send the range (and a sample if useful)?",
        ),
        "RESTAURANT": (
            f"Kitchen coffee supply for {company}",
            f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. We supply Purity Beans to restaurants that want a consistent cup for guests without extra barista load.\n\nMay I send the range for {company} to evaluate?",
        ),
        "CANTEEN": (
            f"Canteen coffee supply — {company}",
            f"Hi {n},\n\nWe supply Purity Beans in bulk for staff canteens and industrial kitchens. Consistent taste, simple prep, B2B refill.\n\nWould a catalogue and pack sizes help {company}?",
        ),
        "HOSPITAL": (
            f"Visitor and staff coffee — {company}",
            f"Hi {n},\n\nI’m Hiten from Pure Pantry Provisions. Hospitals typically need a reliable pantry/visitor coffee that isn’t espresso-dependent.\n\nIf {company} handles F&B or pantry, may I send the range?",
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
    if which == "intro":
        return intro.get(cat, intro["UNKNOWN"])
    if which == "nudge":
        return (
            f"Quick check-in — Purity Beans for {company}",
            f"Hi {n},\n\nJust a short note in case my earlier mail on Purity Beans was easy to miss. "
            f"We work with selected {audience} in {city}.\n\nIf coffee sourcing is relevant, I can send the range — no pitch call required.",
        )
    if which == "proof":
        proofs = {
            "CAFE": "cafés use it for rush-hour cups when the machine queue is long",
            "RESTAURANT": "kitchens keep a consistent guest cup without extra barista time",
            "HOTEL": "hotels use it for in-room and banquet service",
            "CANTEEN": "canteens run it as a daily staff staple",
            "HOSPITAL": "hospital pantries use it for staff and visitor service",
            "RETAILER": "retailers stock the jars because the repeat rate is simple",
            "DISTRIBUTOR": "distributors carry the range for kirana and HORECA accounts",
        }
        proof = proofs.get(cat, f"selected {audience} use it as a reliable B2B cup")
        return (
            f"How {audience} use Purity Beans",
            f"Hi {n},\n\nA useful datapoint for {company}: {proof}.\n\nHappy to send the catalogue or a small sample if that would help you judge it.",
        )
    if which == "ask":
        return (
            f"Sample or a 15-minute call — {company}?",
            f"Hi {n},\n\nI’ll keep this direct. Would a sample, a catalogue, or a 15-minute call be useful for {company}?\n\nIf coffee isn’t a fit, a one-line “not now” is enough and I’ll close the file.",
        )
    return (
        f"Should I close the file for {company}?",
        f"Hi {n},\n\nI’ve reached out a few times about Purity Beans and don’t want to add noise.\n\nIf I should close your file, just say so. If there’s a better person or a later window, I’m happy to follow that instead.",
    )


def _wa_text(lead: B2BLead, profile: OutreachProfile) -> str:
    n = _name(lead)
    return (
        f"Hi {n}, Hiten here from Pure Pantry Provisions. We’re introducing Purity Beans "
        f"and thought it may be relevant for {lead.company or 'your business'}. "
        "Would you like me to send the catalogue?"
    )


def _call_followup_text(lead: B2BLead, profile: OutreachProfile) -> str:
    """The b2b_call_followup message — sent once, only to a lead whose
    consent_source is AI_CALL_WHATSAPP_REQUEST (see plan_touch above).

    Deliberately references the call rather than opening cold: this
    prospect already heard the disclosure, already spoke with the AI agent,
    and already asked for this specific message — repeating the cold-open
    introduction from _wa_text() here would read as if the call never
    happened. AiSensy still requires an approved template for this content;
    AISENSY_CAMPAIGN_CALL_FOLLOWUP names it, separate from whatever campaign
    handles other touches, because Meta approves exact template text per
    campaign, not a channel in general.
    """
    n = _name(lead)
    return (
        f"Hi {n}, Hiten here from Pure Pantry Provisions — following up on our call. "
        f"As discussed, here's a bit more on Purity Beans for {lead.company or 'your business'}: "
        "100% coffee, zero chicory, no fillers, FSSAI licensed, PAN-India dispatch. "
        "Happy to send a sample or set up a quick call with the founder — just let me know which."
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


def plan_touch(db: Session, lead: B2BLead, profile: OutreachProfile | None = None) -> dict:
    """
    Chooses the SHAPE of an adaptive touch — which message, on which channel.

    Named plan_touch, not evaluate_next_action. Two functions sharing that name
    is how this module came to re-answer questions decision_engine already
    owned: at a call site `evaluate_next_action(...)` gave no hint which
    authority you were invoking, and the two disagreed — this one let an
    unsubscribed contact through and emailed addresses the trust engine had
    never approved. The name now states the hierarchy: one engine evaluates,
    this plans within its verdict.

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

    if profile.intent in ("SAMPLE_REQUESTED", "MEETING_REQUESTED", "CALLBACK", "WRONG_PERSON"):
        from app.services.outcome_router import next_shape
        s = next_shape(profile.intent)
        return {**s, "channel": "email"}

    if profile.intent in ("OUT_OF_OFFICE", "MACHINE_REPLY", "CALL_LATER"):
        from app.services.outcome_router import next_shape
        s = next_shape(profile.intent)
        return {**s, "channel": None}

    if profile.intent == "EXISTING_SUPPLIER":
        return {"action": "NURTURE", "channel": None, "execute": False,
                "reason": "already supplied — no automatic re-send"}

    if profile.intent == "BOUNCED":
        return {"action": "COOLDOWN", "channel": None, "execute": False,
                "reason": "hard bounce"}

    if profile.intent == "AI_CALL_WHATSAPP_FOLLOWUP":
        # This is the ONLY place a phone number turning into a WhatsApp send
        # is conditioned on the call outcome rather than the number merely
        # existing. consent_status is already EXPLICIT by the time this
        # fires (record_ai_outcome sets it the moment WHATSAPP_OPT_IN is
        # recorded — see founder_call_pipeline.py), so this branch is about
        # picking the right MESSAGE for a lead who just asked for one, not
        # about permission, which was already decided.
        return {
            "action": "SEND_CALL_FOLLOWUP",
            "channel": "whatsapp",
            "reason": "prospect asked for WhatsApp during the AI qualification call",
            "execute": True,
        }

    if profile.intent == "CATALOGUE_REQUESTED":
        # Prefer WA only with recorded consent; else email catalogue.
        consent = (getattr(lead, "consent_status", None) or "UNKNOWN").upper()
        channel = "whatsapp" if consent in ("EXPLICIT", "OPTED_IN") else "email"
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

    # Cadence is NOT decided here.
    #
    # This module used to count proven sends and cap the sequence at two
    # touches, while sequence_engine runs five (intro, nudge, proof, ask,
    # breakup) and evaluate_next_action already consults it. The two disagreed
    # and deadlocked: the engine returned SEND for REBOOT and three hotels,
    # plan_touch answered "cadence complete", and because the selector is
    # fit-ordered those same four occupied every eligible slot on every cycle.
    # Automation was ON and structurally incapable of sending anything.
    #
    # Counting touches is a "may we?" question, and that belongs to the single
    # authority. This function decides only the SHAPE of the touch, so it reads
    # which touch is next in order to choose the message — never to decide
    # whether one is owed.
    seq = _sequence_state(db, lead)
    if seq.get("unavailable"):
        return {
            "action": "FOUNDER_REVIEW",
            "channel": None,
            "reason": "sequence/history state unavailable — refusing automated outreach",
            "execute": False,
        }
    touch = (seq.get("next_touch") or "").lower()
    variant = {
        "intro": "WARM_FIRST_TOUCH",
        "nudge": "WARM_FOLLOW_UP",
        "proof": "WARM_FOLLOW_UP",
        "ask": "WARM_FOLLOW_UP",
        "breakup": "WARM_FOLLOW_UP",
    }.get(touch, "WARM_FIRST_TOUCH" if not seq.get("touches") else "WARM_FOLLOW_UP")

    return {
        "action": variant,
        "channel": "email",
        "reason": f"touch '{touch or 'intro'}' per sequence_engine"
                  f" (#{(seq.get('touches') or 0) + 1})",
        "execute": True,
    }


def _sequence_state(db: Session, lead: B2BLead) -> dict:
    """Which touch the cadence authority says is next. Read-only."""
    try:
        from app.services import sequence_engine as se
        state = se.state(lead, db)
        if not isinstance(state, dict):
            raise RuntimeError("sequence engine returned an invalid state")
        return state
    except Exception as exc:
        print(f"[smart_outreach] sequence state unavailable for lead {lead.id}: "
              f"{exc.__class__.__name__} — refusing to send")
        return {"unavailable": True}



def _mirror_to_workflow_event(db: Session, lead: B2BLead, channel: str,
                              payload: dict) -> None:
    """
    Mirror a proven send into the WorkflowEvent ledger.

    execute_one recorded an OutreachTouch and nothing else, while
    account_graph.can_contact_new counts only WorkflowEvent EMAIL_SENT /
    WHATSAPP_SENT. Automated sends were therefore invisible to the ACCOUNT
    FREQUENCY CAP — the guarantee that stops 27 More Supermarket branches being
    contacted as 27 opportunities. The first live automated send (REBOOT, lead
    272) wrote a touch at 11:23 and left EMAIL_SENT sitting at 41 with its most
    recent row days old.

    Two ledgers again, the same split that hid cadence state earlier. The touch
    table stays as the outreach-domain record; this mirrors the fact into the
    ledger every governance check already reads, so the cap, /health's
    last_successful_send and the sequence engine all see one truth.

    Written WITH proof (to + message_id), so the strict listener keeps it as
    EMAIL_SENT. A send lacking either is renamed EMAIL_SENT_UNPROVEN, which is
    the correct outcome: it should not consume an account slot.
    """
    from app.models.models import WorkflowEvent

    mid = payload.get("message_id") or payload.get("provider_message_id")
    to = payload.get("to")
    if not to:
        to = (
            getattr(lead, "email", None)
            if channel == "email"
            else (getattr(lead, "whatsapp_number", None) or getattr(lead, "phone", None))
        )
    if not mid or not to:
        return
    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="EMAIL_SENT" if channel == "email" else "WHATSAPP_SENT",
        actor="SMART_OUTREACH", channel=channel,
        payload={"to": to, "message_id": mid,
                 "touch_type": payload.get("touch_type"), "automated": True},
        occurred_at=datetime.utcnow()))


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
        _mirror_to_workflow_event(db, lead, channel, payload)
    profile.last_channel = channel
    profile.next_action = action
    profile.updated_at = datetime.utcnow()
    db.flush()
    return touch


def execute_one(db: Session, lead: B2BLead) -> dict:
    profile = classify_lead(db, lead)
    decision = plan_touch(db, lead, profile)
    if not decision["execute"]:
        return {**decision, "status": "SKIPPED"}

    # Hard duplicate guard: never two proven first touches.
    if decision["action"] == "WARM_FIRST_TOUCH" and _proven_email_touches(db, lead.id):
        return {**decision, "status": "SKIPPED", "reason": "duplicate first touch blocked"}
    if decision["action"] == "SEND_CATALOGUE":
        prior = (
            db.query(OutreachTouch)
            .filter(
                OutreachTouch.lead_id == lead.id,
                OutreachTouch.touch_type == "SEND_CATALOGUE",
                OutreachTouch.status.in_(PROVEN_SEND),
            )
            .first()
        )
        if prior:
            return {**decision, "status": "SKIPPED", "reason": "catalogue already sent"}
    if decision["action"] == "SEND_CALL_FOLLOWUP" and _proven_touch(db, lead.id, "SEND_CALL_FOLLOWUP"):
        return {**decision, "status": "SKIPPED", "reason": "call follow-up already sent"}

    if decision["channel"] == "email":
        from app.services.email_sender import build_outreach_email, send_email

        proven_n = len(_proven_email_touches(db, lead.id))
        seq = _sequence_state(db, lead)
        touch_name = (seq.get("next_touch") or ("intro" if proven_n == 0 else "nudge"))
        subject, body = render_email(
            lead, profile,
            1 if proven_n == 0 else 2,
            touch=touch_name,
        )
        if decision["action"] == "SEND_CATALOGUE":
            import os

            url = (os.getenv("PURITY_BEANS_CATALOGUE_URL") or os.getenv("CATALOGUE_URL") or "").strip()
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
        import os

        from app.services.whatsapp_sender import send_whatsapp

        if decision["action"] == "SEND_CALL_FOLLOWUP":
            text = _call_followup_text(lead, profile)
            # Own campaign, not the generic one: Meta approves exact template
            # text per campaign, and this content differs from every other
            # WhatsApp touch this system sends. Falls back to the shared
            # campaign only if a dedicated one was never configured, so this
            # doesn't hard-fail a deployment that hasn't set it up yet.
            campaign = (os.getenv("AISENSY_CAMPAIGN_CALL_FOLLOWUP") or "").strip() or None
            template_key = "call_followup"
        else:
            text = _wa_text(lead, profile)
            campaign = None
            template_key = "catalogue_request"

        result = send_whatsapp(lead, text, campaign_name=campaign)
        _record(
            db,
            lead,
            profile,
            "whatsapp",
            decision["action"],
            result.status.upper(),
            template_key,
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


# Actions that can actually put a message in front of a buyer.
#
# An ALLOWLIST, not a denylist: a denylist of ("SUPPRESS","WAIT","NONE","ENRICH")
# let DRAFT_ONLY through, and DRAFT_ONLY is precisely "we may write this but not
# send it". Hotel M.S Residency reached SMTP that way and was refused by the
# trust gate — a wasted slot and a log line that reads like a failure when it
# was the system working. Any action added later is excluded until someone
# decides it belongs here.
SEND_ELIGIBLE_ACTIONS = frozenset({
    "SEND", "SEND_INTRO", "SEND_CATALOGUE", "SEND_PRICING",
    "SEND_SAMPLE", "SEND_WHATSAPP",
})


SCAN_FACTOR = 8


def select_candidates(db: Session, limit: int = 20) -> list[B2BLead]:
    """Who this cycle should even look at, in the order it should look.

    ONE selector, used by both executors. outreach_lifecycle.run_automatic_cycle
    had its own — `order_by(score.desc()).limit(20)` — and score is 0 on nearly
    every row, so it returned the same 20 uncontactable leads on every cycle
    forever. Two selectors for one question is the duplication this codebase
    keeps paying for; this is the shared one, and it is tested.

    Three rules, in order:

      1. A channel must exist. Absent this, 16 of 20 slots once went to leads
         with no address, ranked and fetched only to be told so.
      2. Contactable before well-fitting. Measured on production 2026-09-18:
         1,467 leads had a channel, the first one any channel could actually
         reach ranked 904th, and the window was 160 — so both executors
         selected zero every cycle while reporting a healthy run.
      3. Fit orders what remains, and coffee_buying_score is the real evidence
         (cafe 95, hotel 90, restaurant 70, kirana 65, office 45). 0 means
         UNCLASSIFIED, not low fit, so those sort last but are never dropped.

    The window is bounded (limit * SCAN_FACTOR) so a cycle cannot walk 1,800
    leads looking for work. This decides who is CONSIDERED; evaluate_next_action
    still decides who may be contacted, per lead, in the caller.
    """
    return (
        db.query(B2BLead)
        .filter(B2BLead.contact_status.notin_(["OPTED_OUT", "DO_NOT_CONTACT", "BOUNCED"]))
        .filter(B2BLead.status.notin_(["DO_NOT_CONTACT", "CLOSED_LOST", "DISQUALIFIED",
                                       "ORDER_WON"]))
        .filter(
            ((B2BLead.email.isnot(None)) & (B2BLead.email != ""))
            | ((B2BLead.whatsapp_number.isnot(None)) & (B2BLead.whatsapp_number != ""))
        )
        .order_by(_contactable_first(), B2BLead.coffee_buying_score.desc().nullslast(),
                  B2BLead.score.desc(), B2BLead.id.asc())
        .limit(max(limit, limit * SCAN_FACTOR))
        .all()
    )


def run_cycle(db: Session, limit: int = 20) -> dict:
    """Classify and execute due outreach without founder approval.

    `limit` counts SEND-ELIGIBLE leads, not raw candidates.

    It used to take the top `limit` by fit and then discover most of them were
    uncontactable: the first live cycle processed 20 and found 16 with no
    address at all, 1 refused by the trust gate and 1 with no email — one real
    send out of twenty. The nominal "20 per cycle" and the 10/hour cap were
    describing work that mostly could not happen, so both were meaningless as
    rate controls.

    Now the pool is filtered before it is ranked: a channel must exist, the
    contact must not be suppressed, and the lead must not be in a terminal
    state. Leads are then walked in fit order and executed until `limit`
    ELIGIBLE ones have been handled or the scan window is exhausted.

    The window is bounded (limit * SCAN_FACTOR) so a cycle cannot walk 1,800
    leads looking for work. Deliberately not solved by raising the batch size:
    the objective is the best eligible opportunities, not the most emails.

    Never invents a send: every provider result is persisted and every blocked
    or unconfigured path is explicit. Negative/opted-out contacts stop.
    """
    candidates = select_candidates(db, limit)

    leads, skipped_ineligible = [], 0
    for lead in candidates:
        if len(leads) >= limit:
            break
        # The single authority decides eligibility; this only asks it early
        # instead of after fetching and executing.
        try:
            from app.services.decision_engine import evaluate_next_action
            verdict = evaluate_next_action(lead, db)
        except Exception:
            # A gate that cannot answer is not permission — skip, do not send.
            skipped_ineligible += 1
            continue
        if verdict["action"] not in SEND_ELIGIBLE_ACTIONS:
            skipped_ineligible += 1
            continue
        leads.append(lead)

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
    # Truthful funnel, not one number.
    #
    # "processed 20" concealed that 16 of those slots went to leads with no
    # address. A lead is not processed because it was scanned. Each stage is
    # counted separately so the yield is visible and the learning dataset later
    # has honest denominators:
    #
    #   160 scanned -> 12 eligible -> 7 due -> 4 selected -> 4 sent
    #
    # rather than "20 processed".
    from collections import Counter
    by_status = Counter(str(r.get("status") or "UNKNOWN") for r in results)
    return {
        "scanned": len(candidates),          # had a channel and passed the DB filter
        "eligible": len(leads),              # the authority returned a send action
        "selected": len(results),            # actually handed to execute_one
        "sent": by_status.get("SENT", 0),
        "blocked": by_status.get("BLOCKED", 0) + by_status.get("FAILED", 0),
        "deferred": by_status.get("SKIPPED", 0),
        "skipped_ineligible": skipped_ineligible,
        "by_status": dict(by_status),
        # Kept for callers/logs that still read it; equals `selected`.
        "processed": len(results),
        "results": results,
    }
