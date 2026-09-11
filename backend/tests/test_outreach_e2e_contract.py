"""End-to-end outreach safety contracts.

These tests exercise the decision authority, cadence state, smart-outreach
planner, and API wiring together against an isolated database. Providers are
not contacted: this is a dry run of the decision path only.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from conftest import memory_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.models import Base, B2BLead, WorkflowEvent
from app.services.decision_engine import evaluate_next_action
from app.services.sequence_engine import state
from app.services.smart_outreach import classify_lead, plan_touch


@pytest.fixture
def db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def lead(db, **kw):
    defaults = dict(
        company="E2E Cafe",
        city="Abohar",
        division="CAFE",
        email="owner@example.com",
        email_trust="VERIFIED",
        email_confidence=80,
        email_verification_status="VALID",
        website="https://example.com",
        maps_rating=4.5,
        status="DISCOVERED",
        contact_status="CONTACTABLE",
    )
    defaults.update(kw)
    obj = B2BLead(**defaults)
    # The production quality gate requires explicit fit evidence. Keep that
    # requirement intact and make this fixture represent a known-fit prospect.
    obj.coffee_buying_score = 80
    db.add(obj)
    db.commit()
    return obj


def event(db, l, event_type, payload=None, when=None):
    db.add(WorkflowEvent(
        lead_id=l.id,
        event_type=event_type,
        actor="E2E",
        channel="email",
        payload=payload or {},
        occurred_at=when or datetime.utcnow(),
    ))
    db.commit()


def test_cold_verified_contact_can_reach_send_decision(db):
    l = lead(db)
    d = evaluate_next_action(l, db)
    assert d["action"] == "SEND", d
    assert d["blockers"] == [], d


def test_opt_out_is_absolute_across_authority_and_planner(db):
    l = lead(db)
    event(db, l, "UNSUBSCRIBED")
    d = evaluate_next_action(l, db)
    assert d["action"] == "SUPPRESS", d
    p = plan_touch(db, l)
    assert p["execute"] is False, p
    assert p["action"] == "COOLDOWN", p


def test_reply_stops_scheduled_outreach(db):
    l = lead(db)
    event(db, l, "EMAIL_REPLY_RECEIVED", {"intents": ["INTERESTED"], "polarity": "positive", "next_action": "respond", "sla_minutes": 60, "confidence": 95})
    d = evaluate_next_action(l, db)
    assert d["action"] in {"DRAFT_ONLY", "FOUNDER_REVIEW"}, d
    st = state(l, db)
    assert st["active"] is False
    assert st["reason"] == "replied"


def test_unverified_contact_never_becomes_send(db):
    l = lead(db, email_trust="DISCOVERED", email_confidence=20)
    d = evaluate_next_action(l, db)
    assert d["action"] != "SEND", d
    assert "not_sendable" in d["blockers"] or d["action"] in {"DRAFT_ONLY", "ENRICH", "FOUNDER_REVIEW"}, d


def test_sequence_waits_before_followup(db):
    l = lead(db)
    event(db, l, "EMAIL_SENT", {"to": l.email, "message_id": "e2e-1"})
    st = state(l, db)
    assert st["active"] is True
    assert st["next_touch"] == "nudge"
    assert st["ready"] is False
    d = evaluate_next_action(l, db)
    assert d["action"] == "WAIT", d


def test_completed_sequence_does_not_restart(db):
    l = lead(db)
    first = datetime.utcnow() - timedelta(days=30)
    for i in range(5):
        event(db, l, "EMAIL_SENT", {"to": l.email, "message_id": f"e2e-{i}"}, first + timedelta(days=i * 3))
    st = state(l, db)
    assert st["active"] is False
    assert st["reason"] == "completed"
    d = evaluate_next_action(l, db)
    assert d["action"] == "NONE", d


def test_smart_outreach_router_uses_live_decision_authority():
    from app.api.smart_outreach_router import evaluate_next_action as imported, next_action
    from app.services.decision_engine import evaluate_next_action as authority
    assert imported is authority
    assert callable(next_action)


def test_classification_is_not_a_send_permission(db):
    l = lead(db, email_trust="DISCOVERED", email_confidence=20)
    profile = classify_lead(db, l)
    assert profile.category == "CAFE"
    d = evaluate_next_action(l, db)
    assert d["action"] != "SEND"
