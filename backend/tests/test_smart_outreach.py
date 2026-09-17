from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead, WorkflowEvent, CallHistory, LeadInteraction
from app.services.smart_outreach import OutreachProfile, OutreachTouch, classify_lead, plan_touch


def _sendable(**kw):
    """
    A lead the trust engine actually permits sending to.

    These tests exercise classification and cadence, not the trust gate. Built
    without trust fields, every lead is UNSEEN and decision_engine correctly
    refuses — so the tests would have been asserting that an address with no
    provenance may be emailed, which is the bypass this module used to have.
    """
    kw.setdefault("email_trust", "VERIFIED")
    kw.setdefault("email_confidence", 80)
    kw.setdefault("email_source", "FOUNDER_CALL")
    # Category evidence. The record-quality gate treats coffee_buying_score 0
    # as "no evidence matched" -> NEEDS_ENRICHMENT, so a lead built without it
    # is never sendable and these tests would be asserting that an unclassified
    # business may be cold-emailed. 70 is the real score for a restaurant.
    kw.setdefault("coffee_buying_score", 70)
    return B2BLead(**kw)


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # WorkflowEvent belongs in this list: three tests below insert one to give
    # the lead a history, and without the table they failed with
    # "no such table: workflow_events" rather than on anything they assert.
    # An explicit table list is faster than create_all but silently omits
    # whatever a test starts using later, which is how this drifted.
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__,
        WorkflowEvent.__table__,
        OutreachProfile.__table__,
        OutreachTouch.__table__,
        CallHistory.__table__,
        LeadInteraction.__table__,
    ])
    return sessionmaker(bind=engine)()


def test_distributor_classification_uses_business_signal():
    db = _db()
    lead = B2BLead(company="North Star Distribution", division="distributor", industry="FMCG distribution", city="Delhi", status="DISCOVERED")
    db.add(lead)
    db.commit()
    profile = classify_lead(db, lead)
    assert profile.category == "DISTRIBUTOR"
    assert profile.buying_angle == "MARGIN_AND_RANGE"
    assert profile.warmth == "COLD"
    assert profile.category_confidence >= 45


def test_history_changes_intent_without_changing_category():
    db = _db()
    lead = _sendable(company="ABC Retail Mart", division="retail", industry="supermarket", email="buyer@abcretail.in", status="DISCOVERED")
    db.add(lead)
    db.commit()
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(lead_id=lead.id, event_type="CATALOGUE_REQUESTED", actor="PROSPECT", channel="email", payload={}, occurred_at=datetime.utcnow()))
    db.commit()
    profile = classify_lead(db, lead)
    assert profile.category == "RETAILER"
    assert profile.warmth == "WARM"
    assert profile.intent == "CATALOGUE_REQUESTED"
    action = plan_touch(db, lead, profile)
    assert action["action"] == "SEND_CATALOGUE"


def test_negative_history_stops_automation():
    db = _db()
    lead = B2BLead(company="Stop Co", division="corporate", status="DISCOVERED")
    db.add(lead)
    db.commit()
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(lead_id=lead.id, event_type="UNSUBSCRIBED", actor="PROSPECT", channel="email", payload={}, occurred_at=datetime.utcnow()))
    db.commit()
    decision = plan_touch(db, lead)
    assert decision["action"] == "COOLDOWN"
    assert decision["execute"] is False


def test_cold_lead_defaults_to_email_warming():
    db = _db()
    lead = _sendable(company="Cold Office Pvt Ltd", division="corporate", industry="IT office", email="buyer@coldoffice.in", status="DISCOVERED")
    db.add(lead)
    db.commit()
    decision = plan_touch(db, lead)
    assert decision["action"] == "WARM_FIRST_TOUCH"
    assert decision["channel"] == "email"
    assert decision["execute"] is True


def test_followup_only_after_cadence():
    db = _db()
    lead = _sendable(company="Followup Co", division="corporate", email="buyer@followupco.in", status="EMAIL_SENT")
    db.add(lead)
    db.commit()
    from app.models.models import WorkflowEvent
    # A PROVEN send. payload={} is renamed EMAIL_SENT_UNPROVEN by the strict
    # send-proof listener, and cadence deliberately ignores unproven sends:
    # following up on one means emailing "just following up on my email" to
    # someone who may never have received a first one. The recipient and
    # provider message-id are what make this a send that actually happened.
    db.add(WorkflowEvent(lead_id=lead.id, event_type="EMAIL_SENT", actor="SYSTEM", channel="email",
                         payload={"to": lead.email, "message_id": "<followup-co-1@test>"},
                         occurred_at=datetime.utcnow() - timedelta(days=4)))
    db.commit()
    decision = plan_touch(db, lead)
    assert decision["action"] == "WARM_FOLLOW_UP"
    assert decision["channel"] == "email"


def test_call_sourced_whatsapp_opt_in_gets_the_call_followup_not_a_cold_open():
    """The exact distinction the user asked to preserve: a phone number
    existing is not permission. Goes through the REAL
    founder_call_pipeline.record_ai_outcome(), not a hand-built lead, because
    that function's WHATSAPP_OPT_IN branch does two things a hand-built lead
    would silently skip: it sets consent_source=AI_CALL_WHATSAPP_REQUEST, and
    it writes the NEXT_ACTION_SET(action=SEND_WHATSAPP) event decision_engine's
    commitment system needs to outrank the trust/record-quality gates for a
    promise already made. Before either existed, this lead had no
    distinguishing signal and fell to the generic COLD/WARM_FIRST_TOUCH path —
    the same cold-open message a lead who has never been contacted gets,
    discarding the fact a live qualification call already happened."""
    from app.services.founder_call_pipeline import record_ai_outcome

    db = _db()
    lead = _sendable(
        company="Ludhiana Coffee House", division="cafe", phone="9876500001",
        whatsapp_number="9876500001", status="DISCOVERED",
    )
    db.add(lead)
    db.commit()

    record_ai_outcome(lead, db, "WHATSAPP_OPT_IN", summary="asked for details on WhatsApp")
    db.commit()
    assert lead.consent_source == "AI_CALL_WHATSAPP_REQUEST"

    profile = classify_lead(db, lead)
    assert profile.intent == "AI_CALL_WHATSAPP_FOLLOWUP"
    assert profile.warmth == "HOT"

    decision = plan_touch(db, lead, profile)
    assert decision["action"] == "SEND_CALL_FOLLOWUP"
    assert decision["channel"] == "whatsapp"
    assert decision["execute"] is True


def test_a_bare_phone_number_with_no_call_outcome_is_not_treated_as_opt_in():
    """The other half of the distinction: merely having a phone/WhatsApp
    number on file — discovered, never called — must not produce the
    call-followup intent. consent_source is unset, so this is exactly the
    'phone number discovered' state that must NOT look like 'call outcome
    established consent'."""
    db = _db()
    lead = _sendable(
        company="Amritsar General Store", division="retailer",
        whatsapp_number="9876500002", status="DISCOVERED",
    )
    db.add(lead)
    db.commit()

    profile = classify_lead(db, lead)
    assert profile.intent != "AI_CALL_WHATSAPP_FOLLOWUP"


def test_call_followup_intent_does_not_repeat_once_the_touch_is_proven():
    """One-shot: after the follow-up has actually gone out, the same lead
    must fall through to the normal warmth/intent ladder on the next
    classification cycle rather than being classified as still-owed a
    follow-up forever."""
    db = _db()
    lead = _sendable(
        company="Jalandhar Snack Bar", division="cafe",
        whatsapp_number="9876500003",
        consent_status="EXPLICIT", consent_source="AI_CALL_WHATSAPP_REQUEST",
        status="DISCOVERED",
    )
    db.add(lead)
    db.commit()

    profile = classify_lead(db, lead)
    db.add(OutreachTouch(
        lead_id=lead.id, profile_id=profile.id, channel="whatsapp",
        touch_type="SEND_CALL_FOLLOWUP", template_key="call_followup",
        status="SENT", occurred_at=datetime.utcnow(),
    ))
    db.commit()

    profile2 = classify_lead(db, lead)
    assert profile2.intent != "AI_CALL_WHATSAPP_FOLLOWUP"
