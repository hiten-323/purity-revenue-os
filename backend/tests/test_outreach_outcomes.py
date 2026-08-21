"""Cafe split, sequence copy, and reply outcomes. No network, no live sends."""
from datetime import datetime

from app.services.outcome_router import next_shape
from app.services.outreach_lifecycle import infer_intent
from app.services.smart_outreach import classify_lead, render_email
from app.models.models import B2BLead, WorkflowEvent
from app.database.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.services.smart_outreach import OutreachProfile, OutreachTouch


def _event(event_type, payload=None, channel="email"):
    class E:
        pass

    e = E()
    e.event_type = event_type
    e.payload = payload or {}
    e.channel = channel
    e.lead_id = 1
    e.id = 1
    e.occurred_at = datetime.utcnow()
    return e


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__, WorkflowEvent.__table__,
        OutreachProfile.__table__, OutreachTouch.__table__,
    ])
    return sessionmaker(bind=engine)()


def test_cafe_is_not_horeca():
    db = _db()
    lead = B2BLead(company="Brew House", division="cafe", industry="coffee shop",
                   city="Abohar", status="DISCOVERED", searched_category="cafe")
    db.add(lead)
    db.commit()
    profile = classify_lead(db, lead)
    assert profile.category == "CAFE"
    subject, body = render_email(lead, profile, 1, touch="intro")
    assert "café" in body.lower() or "cafe" in body.lower()
    assert "horeca" not in body.lower()


def test_hospital_is_not_corporate():
    db = _db()
    lead = B2BLead(company="Adesh Hospital", division="hospital", industry="hospital",
                   city="Bathinda", status="DISCOVERED")
    db.add(lead)
    db.commit()
    assert classify_lead(db, lead).category == "HOSPITAL"


def test_restaurant_and_canteen_distinct():
    db = _db()
    r = B2BLead(company="Spice Dhaba", industry="restaurant", city="Ludhiana", status="DISCOVERED")
    c = B2BLead(company="Plant Canteen", industry="factory canteen", city="Ludhiana", status="DISCOVERED")
    db.add_all([r, c])
    db.commit()
    assert classify_lead(db, r).category == "RESTAURANT"
    assert classify_lead(db, c).category == "CANTEEN"


def test_sequence_touches_are_not_the_same_email():
    class L:
        contact_name = "Asha"
        company = "Cafe Mocha"
        city = "Pune"

    class P:
        category = "CAFE"

    intro_s, intro_b = render_email(L(), P(), 1, touch="intro")
    nudge_s, nudge_b = render_email(L(), P(), 2, touch="nudge")
    proof_s, proof_b = render_email(L(), P(), 3, touch="proof")
    ask_s, _ = render_email(L(), P(), 4, touch="ask")
    brk_s, _ = render_email(L(), P(), 5, touch="breakup")
    assert intro_s != nudge_s != proof_s
    assert "just following up" not in proof_b.lower()
    assert "sample" in ask_s.lower() or "call" in ask_s.lower()
    assert "close" in brk_s.lower()
    assert "Cafe Mocha" in intro_s or "Café" in intro_b or "café" in intro_b.lower()


def test_ooo_is_not_interested():
    assert (
        infer_intent(_event("EMAIL_REPLY_RECEIVED", {
            "subject": "Out of Office",
            "body": "I am out of office until 28 Aug. This is an automatic reply.",
        }))
        == "OUT_OF_OFFICE"
    )
    assert next_shape("OUT_OF_OFFICE")["execute"] is False
    assert next_shape("OUT_OF_OFFICE")["action"] == "WAIT"


def test_catalogue_still_yes():
    assert infer_intent(_event("EMAIL_REPLY_RECEIVED", {"body": "Yes, send the catalogue"})) == "CATALOGUE_REQUESTED"
    assert next_shape("CATALOGUE_REQUESTED")["action"] == "SEND_CATALOGUE"
    assert next_shape("CATALOGUE_REQUESTED")["execute"] is True


def test_pricing_and_sample_are_founder():
    assert infer_intent(_event("WHATSAPP_REPLY", {"body": "What is your wholesale price and MOQ?"}, "whatsapp")) == "PRICING_REQUESTED"
    assert next_shape("PRICING_REQUESTED")["action"] == "FOUNDER_REVIEW"
    assert next_shape("SAMPLE_REQUESTED")["action"] == "FOUNDER_REVIEW"
    assert next_shape("MEETING_REQUESTED")["execute"] is False


def test_opt_out_and_bounce():
    assert infer_intent(_event("EMAIL_REPLY_RECEIVED", {"body": "Please remove me"})) == "OPTED_OUT"
    assert next_shape("BOUNCED")["execute"] is False
    assert next_shape("OPTED_OUT")["action"] == "COOLDOWN"
