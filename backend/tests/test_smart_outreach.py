from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead
from app.services.smart_outreach import OutreachProfile, OutreachTouch, classify_lead, evaluate_next_action


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[B2BLead.__table__, OutreachProfile.__table__, OutreachTouch.__table__])
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
    lead = B2BLead(company="ABC Retail Mart", division="retail", industry="supermarket", status="DISCOVERED")
    db.add(lead)
    db.commit()
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(lead_id=lead.id, event_type="CATALOGUE_REQUESTED", actor="PROSPECT", channel="email", payload={}, occurred_at=datetime.utcnow()))
    db.commit()
    profile = classify_lead(db, lead)
    assert profile.category == "RETAILER"
    assert profile.warmth == "WARM"
    assert profile.intent == "CATALOGUE_REQUESTED"
    action = evaluate_next_action(db, lead, profile)
    assert action["action"] == "SEND_CATALOGUE"


def test_negative_history_stops_automation():
    db = _db()
    lead = B2BLead(company="Stop Co", division="corporate", status="DISCOVERED")
    db.add(lead)
    db.commit()
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(lead_id=lead.id, event_type="UNSUBSCRIBED", actor="PROSPECT", channel="email", payload={}, occurred_at=datetime.utcnow()))
    db.commit()
    decision = evaluate_next_action(db, lead)
    assert decision["action"] == "COOLDOWN"
    assert decision["execute"] is False


def test_cold_lead_defaults_to_email_warming():
    db = _db()
    lead = B2BLead(company="Cold Office Pvt Ltd", division="corporate", industry="IT office", email="buyer@example.com", status="DISCOVERED")
    db.add(lead)
    db.commit()
    decision = evaluate_next_action(db, lead)
    assert decision["action"] == "WARM_FIRST_TOUCH"
    assert decision["channel"] == "email"
    assert decision["execute"] is True


def test_followup_only_after_cadence():
    db = _db()
    lead = B2BLead(company="Followup Co", division="corporate", email="buyer@example.com", status="EMAIL_SENT")
    db.add(lead)
    db.commit()
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(lead_id=lead.id, event_type="EMAIL_SENT", actor="SYSTEM", channel="email", payload={}, occurred_at=datetime.utcnow() - timedelta(days=4)))
    db.commit()
    decision = evaluate_next_action(db, lead)
    assert decision["action"] == "WARM_FOLLOW_UP"
    assert decision["channel"] == "email"
