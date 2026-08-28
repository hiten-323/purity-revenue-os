from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead, WorkflowEvent
from app.services.smart_outreach import OutreachProfile, OutreachTouch, classify_lead, plan_touch, execute_one
from app.services.whatsapp_sender import consent_check


def _sendable(**kw):
    kw.setdefault("email_trust", "VERIFIED")
    kw.setdefault("email_confidence", 80)
    kw.setdefault("email_source", "FOUNDER_CALL")
    kw.setdefault("coffee_buying_score", 70)
    return B2BLead(**kw)


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[B2BLead.__table__, WorkflowEvent.__table__, OutreachProfile.__table__, OutreachTouch.__table__])
    return sessionmaker(bind=engine)()


def test_distributor_classification_uses_business_signal():
    db = _db()
    lead = B2BLead(company="North Star Distribution", division="distributor", industry="FMCG distribution", city="Delhi", status="DISCOVERED")
    db.add(lead); db.commit()
    profile = classify_lead(db, lead)
    assert profile.category == "DISTRIBUTOR"
    assert profile.buying_angle == "MARGIN_AND_RANGE"
    assert profile.warmth == "COLD"
    assert profile.category_confidence >= 45


def test_history_changes_intent_without_changing_category():
    db = _db()
    lead = _sendable(company="ABC Retail Mart", division="retail", industry="supermarket", email="buyer@abcretail.in", status="DISCOVERED")
    db.add(lead); db.commit()
    db.add(WorkflowEvent(lead_id=lead.id, event_type="CATALOGUE_REQUESTED", actor="PROSPECT", channel="email", payload={}, occurred_at=datetime.utcnow())); db.commit()
    profile = classify_lead(db, lead)
    assert profile.category == "RETAILER"
    assert profile.warmth == "WARM"
    assert profile.intent == "CATALOGUE_REQUESTED"
    action = plan_touch(db, lead, profile)
    assert action["action"] == "SEND_CATALOGUE"


def test_catalogue_request_is_idempotent_after_fulfilment():
    db = _db()
    lead = _sendable(company="ABC Retail Mart", division="retail", industry="supermarket", email="buyer@abcretail.in", status="DISCOVERED")
    db.add(lead); db.commit()
    requested_at = datetime.utcnow() - timedelta(minutes=10)
    db.add(WorkflowEvent(lead_id=lead.id, event_type="CATALOGUE_REQUESTED", actor="PROSPECT", channel="email", payload={}, occurred_at=requested_at)); db.commit()
    profile = classify_lead(db, lead)
    first = plan_touch(db, lead, profile)
    assert first["action"] == "SEND_CATALOGUE"
    db.add(OutreachTouch(lead_id=lead.id, profile_id=profile.id, channel="email", touch_type="SEND_CATALOGUE", template_key="catalogue_email", status="SENT", provider_message_id="catalogue-1", payload={"message_id":"catalogue-1","to":lead.email}, occurred_at=datetime.utcnow() - timedelta(minutes=5))); db.commit()
    second = plan_touch(db, lead, profile)
    assert second["action"] == "CONTINUE_CONVERSATION"
    assert second["execute"] is False


def test_negative_history_stops_automation():
    db = _db()
    lead = B2BLead(company="Stop Co", division="corporate", status="DISCOVERED")
    db.add(lead); db.commit()
    db.add(WorkflowEvent(lead_id=lead.id, event_type="UNSUBSCRIBED", actor="PROSPECT", channel="email", payload={}, occurred_at=datetime.utcnow())); db.commit()
    decision = plan_touch(db, lead)
    assert decision["action"] == "COOLDOWN"
    assert decision["execute"] is False


def test_cold_lead_defaults_to_email_warming():
    db = _db()
    lead = _sendable(company="Cold Office Pvt Ltd", division="corporate", industry="IT office", email="buyer@coldoffice.in", status="DISCOVERED")
    db.add(lead); db.commit()
    decision = plan_touch(db, lead)
    assert decision["action"] == "WARM_FIRST_TOUCH"
    assert decision["channel"] == "email"
    assert decision["execute"] is True


def test_followup_only_after_cadence():
    db = _db()
    lead = _sendable(company="Followup Co", division="corporate", email="buyer@followupco.in", status="EMAIL_SENT")
    db.add(lead); db.commit()
    db.add(WorkflowEvent(lead_id=lead.id, event_type="EMAIL_SENT", actor="SYSTEM", channel="email", payload={"to":lead.email,"message_id":"<followup-co-1@test>"}, occurred_at=datetime.utcnow() - timedelta(days=4))); db.commit()
    decision = plan_touch(db, lead)
    assert decision["action"] == "WARM_FOLLOW_UP"
    assert decision["channel"] == "email"


def test_whatsapp_requires_explicit_opt_in():
    lead = B2BLead(company="Cold WhatsApp Lead", phone="9876543210", consent_status="UNKNOWN")
    allowed, reason = consent_check(lead)
    assert allowed is False
    assert "opt-in" in reason.lower()


def test_whatsapp_does_not_treat_implied_b2b_as_opt_in():
    lead = B2BLead(company="B2B Lead", phone="9876543210", consent_status="IMPLIED_B2B")
    allowed, reason = consent_check(lead)
    assert allowed is False
    assert "opt-in" in reason.lower()


def test_whatsapp_accepts_explicit_opt_in():
    lead = B2BLead(company="Opted In", phone="9876543210", consent_status="EXPLICIT")
    allowed, _ = consent_check(lead)
    assert allowed is True


def test_execute_one_respects_global_kill_switch(monkeypatch):
    monkeypatch.setenv("AUTO_OUTREACH_ENABLED", "0")
    monkeypatch.setenv("SMART_OUTREACH_ENABLED", "0")
    db = _db()
    lead = _sendable(company="Kill Switch Co", division="corporate", email="buyer@killswitch.example", status="DISCOVERED")
    db.add(lead); db.commit()
    result = execute_one(db, lead)
    assert result["status"] == "DISABLED"
    assert result["execute"] is False
