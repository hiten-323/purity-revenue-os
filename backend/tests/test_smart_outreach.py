from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead, WorkflowEvent
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
