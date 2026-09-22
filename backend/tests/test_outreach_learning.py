"""Tests for cross-lead outreach learning.

Learning must shape the next lead, never select/suppress a channel.
"""
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app.models.models import (
    B2BLead,
    Base,
    LearnedPattern,
    ObjectionLearning,
    WorkflowEvent,
)
from app.services.smart_outreach import OutreachProfile, OutreachTouch
from app.services.outreach_learning import build_learning_context
from conftest import memory_engine


def _db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _lead(db, company, category="CAFE", **kw):
    lead = B2BLead(
        company=company,
        segment="cafe",
        phone=kw.pop("phone", None),
        email=kw.pop("email", None),
        **kw,
    )
    db.add(lead)
    db.flush()
    db.add(OutreachProfile(
        lead_id=lead.id,
        category=category,
        category_confidence=90,
        warmth="CONTACTED",
    ))
    db.flush()
    return lead


def test_next_lead_learns_from_previous_call_and_email():
    engine, db = _db()
    try:
        previous = _lead(
            db,
            "Previous Cafe",
            email="previous@example.com",
            phone="9876500001",
            call_outcome_last="SAMPLE_REQUESTED",
        )
        db.add(OutreachTouch(
            lead_id=previous.id,
            profile_id=db.query(OutreachProfile).filter_by(lead_id=previous.id).one().id,
            channel="email",
            touch_type="WARM_FIRST_TOUCH",
            template_key="typed_day0",
            status="DELIVERED",
            occurred_at=datetime.utcnow(),
        ))
        db.add(WorkflowEvent(
            lead_id=previous.id,
            event_type="EMAIL_REPLY_RECEIVED",
            channel="email",
            payload={"intent": "SAMPLE_REQUESTED"},
            occurred_at=datetime.utcnow(),
        ))

        # Five comparable observations are required before a pattern becomes
        # actionable. This prevents one anecdote from becoming a hard rule.
        for n in range(4):
            p = _lead(
                db,
                f"Comparable Cafe {n}",
                email=f"c{n}@example.com",
                phone=f"98765000{10+n}",
                call_outcome_last="SAMPLE_REQUESTED",
            )
            db.add(OutreachTouch(
                lead_id=p.id,
                profile_id=db.query(OutreachProfile).filter_by(lead_id=p.id).one().id,
                channel="email",
                touch_type="WARM_FIRST_TOUCH",
                template_key="typed_day0",
                status="DELIVERED",
            ))
            db.add(WorkflowEvent(
                lead_id=p.id,
                event_type="EMAIL_REPLY_RECEIVED",
                channel="email",
                payload={"intent": "SAMPLE_REQUESTED"},
            ))

        db.add(LearnedPattern(
            scope="category_channel",
            key="CAFE:email",
            metric="reply_rate_pct",
            value=25.0,
            sample_size=5,
            wins=5,
        ))

        current = _lead(
            db,
            "Next Cafe",
            email="next@example.com",
            phone="9876500099",
            category="CAFE",
        )
        db.commit()

        ctx = build_learning_context(db, current)
        assert ctx["learned_intent"] == "SAMPLE_REQUESTED"
        assert ctx["recommended_cta"] == "sample"
        assert ctx["evidence_count"] >= 5
        assert ctx["calls"]["positive"] >= 5
        assert "sample" in ctx["recommended_call_question"].lower()
    finally:
        db.close()
        engine.dispose()


def test_thin_evidence_does_not_adapt():
    engine, db = _db()
    try:
        previous = _lead(
            db,
            "One Cafe",
            email="one@example.com",
            phone="9876500011",
            call_outcome_last="SAMPLE_REQUESTED",
        )
        current = _lead(
            db,
            "Next Cafe",
            email="next@example.com",
            phone="9876500012",
        )
        db.commit()

        ctx = build_learning_context(db, current)
        assert ctx["learned_intent"] is None
        assert ctx["evidence_count"] == 0
        assert "baseline" in ctx["summary"]
    finally:
        db.close()
        engine.dispose()


def test_learning_never_changes_channel_eligibility():
    # This is an architectural contract: learning returns guidance only.
    engine, db = _db()
    try:
        current = _lead(
            db,
            "Next Cafe",
            email="next@example.com",
            phone="9876500013",
        )
        db.commit()
        ctx = build_learning_context(db, current)
        assert "email" in ctx
        assert "calls" in ctx
        assert "eligible" not in ctx
        assert "suppress" not in ctx
    finally:
        db.close()
        engine.dispose()
