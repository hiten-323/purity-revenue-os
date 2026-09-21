"""Closed-loop outreach learning tests. No network and no provider calls."""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead, WorkflowEvent, LearnedPattern
from app.services.outreach_learning import (
    MIN_ADAPTATION_SAMPLE,
    rebuild_learning,
    recommendations_for_lead,
)


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__, WorkflowEvent.__table__, LearnedPattern.__table__,
    ])
    return sessionmaker(bind=engine)()


def _lead(db, company, division="cafe"):
    lead = B2BLead(company=company, division=division, industry="coffee shop")
    db.add(lead)
    db.flush()
    return lead


def test_learning_separates_email_and_call_evidence():
    db = _db()
    leads = [_lead(db, f"Cafe {i}") for i in range(40)]

    for i, lead in enumerate(leads):
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="EMAIL_SENT", channel="email",
            actor="SMART_OUTREACH", payload={"message_id": f"<m{i}>"},
            occurred_at=datetime.utcnow(),
        ))
        if i < 8:
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="EMAIL_REPLY_RECEIVED", channel="email",
                actor="SYSTEM", payload={"body": "Please send details"},
                occurred_at=datetime.utcnow(),
            ))
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="FOUNDER_CALL_PIPELINE", channel="phone",
            actor="AI", after_status="AI_CALL_ATTEMPTED",
            occurred_at=datetime.utcnow(),
        ))
        if i < 20:
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="FOUNDER_CALL_PIPELINE", channel="phone",
                actor="AI", after_status="AI_INTEREST_DETECTED",
                occurred_at=datetime.utcnow(),
            ))

    db.commit()
    result = rebuild_learning(db, min_sample=5)
    assert result["patterns_updated"] > 0

    email = db.query(LearnedPattern).filter_by(
        scope="outreach_learning", key="cafe:email", metric="reply_rate_pct"
    ).one()
    call = db.query(LearnedPattern).filter_by(
        scope="outreach_learning", key="cafe:ai_call", metric="positive_rate_pct"
    ).one()

    assert email.sample_size == 40
    assert email.wins == 8
    assert email.value == 20.0
    assert call.sample_size == 40
    assert call.wins == 20
    assert call.value == 50.0


def test_learning_changes_order_but_never_channel_eligibility():
    db = _db()
    lead = _lead(db, "Learning Cafe")

    db.add_all([
        LearnedPattern(
            scope="outreach_learning", key="cafe:email",
            metric="reply_rate_pct", value=30, sample_size=30, wins=9,
        ),
        LearnedPattern(
            scope="outreach_learning", key="cafe:ai_call",
            metric="positive_rate_pct", value=10, sample_size=30, wins=3,
        ),
    ])
    db.commit()

    recommendation = recommendations_for_lead(db, lead)
    assert recommendation["evidence_mature"] is True
    assert recommendation["channel_order"] == ["email", "call"]
    assert MIN_ADAPTATION_SAMPLE == 20


def test_thin_evidence_does_not_adapt():
    db = _db()
    lead = _lead(db, "Thin Evidence Cafe")

    db.add_all([
        LearnedPattern(
            scope="outreach_learning", key="cafe:email",
            metric="reply_rate_pct", value=90, sample_size=3, wins=3,
        ),
        LearnedPattern(
            scope="outreach_learning", key="cafe:ai_call",
            metric="positive_rate_pct", value=1, sample_size=3, wins=0,
        ),
    ])
    db.commit()

    recommendation = recommendations_for_lead(db, lead)
    assert recommendation["evidence_mature"] is False
    assert recommendation["channel_order"] == ["call", "email"]
