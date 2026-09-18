"""
run_cycle has to look at leads it can actually send to.

Production, 2026-09-18: 1,467 leads had a channel, the scan window was 160,
and the first lead any channel could actually reach ranked 904th — because
fit (coffee_buying_score) outranked contactability. Both outreach executors
selected zero leads every cycle while reporting a healthy run. Turning
Smart Outreach "on" in that state sends nothing, which is the failure mode
this file exists to prevent from coming back.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead
from app.services.smart_outreach import OutreachProfile, OutreachTouch, _contactable_first


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__, OutreachProfile.__table__, OutreachTouch.__table__,
    ])
    return sessionmaker(bind=engine)()


def _ordered(db, limit=50):
    """The candidate ordering run_cycle uses, without executing anything."""
    return (
        db.query(B2BLead)
        .order_by(_contactable_first(), B2BLead.coffee_buying_score.desc().nullslast(),
                  B2BLead.score.desc(), B2BLead.id.asc())
        .limit(limit)
        .all()
    )


def test_a_reachable_lead_outranks_a_better_fitting_unreachable_one():
    """The production shape: high-fit leads with no usable address burying the
    handful that can actually be emailed."""
    db = _db()
    for i in range(30):
        db.add(B2BLead(company=f"High Fit No Address {i}", coffee_buying_score=95,
                       email=f"buyer{i}@example.in", email_trust="PURGED"))
    db.add(B2BLead(company="Low Fit But Sendable", coffee_buying_score=0,
                   email="owner@sendable.in", email_trust="VERIFIED", email_confidence=80))
    db.commit()

    first = _ordered(db)[0]
    assert first.company == "Low Fit But Sendable"


def test_whatsapp_consent_counts_as_reachable_too():
    """Contactability is not email-only: a lead with recorded WhatsApp consent
    is reachable even with no email trust at all."""
    db = _db()
    for i in range(10):
        db.add(B2BLead(company=f"High Fit {i}", coffee_buying_score=95,
                       email=f"x{i}@example.in", email_trust="DISCOVERED"))
    db.add(B2BLead(company="WhatsApp Opted In", coffee_buying_score=0,
                   whatsapp_number="9876500001", consent_status="EXPLICIT"))
    db.commit()

    assert _ordered(db)[0].company == "WhatsApp Opted In"


def test_fit_still_orders_within_the_reachable_group():
    """Contactability decides who is looked at; fit still decides the order
    among them, so the best opportunity is still worked first."""
    db = _db()
    db.add(B2BLead(company="Reachable Cafe", coffee_buying_score=95,
                   email="cafe@example.in", email_trust="VERIFIED", email_confidence=80))
    db.add(B2BLead(company="Reachable Office", coffee_buying_score=45,
                   email="office@example.in", email_trust="VERIFIED", email_confidence=80))
    db.commit()

    assert [l.company for l in _ordered(db)][:2] == ["Reachable Cafe", "Reachable Office"]


def test_unreachable_leads_are_ranked_not_dropped():
    """A preference, not a filter. An unreachable lead still appears — it
    becomes reachable the moment trust or consent lands, with no second list
    to keep in sync."""
    db = _db()
    db.add(B2BLead(company="Sendable", email="a@example.in",
                   email_trust="VERIFIED", email_confidence=80))
    db.add(B2BLead(company="Not Yet", email="b@example.in", email_trust="DISCOVERED"))
    db.commit()

    assert [l.company for l in _ordered(db)] == ["Sendable", "Not Yet"]
