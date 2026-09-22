"""Deterministic in-memory tests for Outreach Intelligence V1."""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead, LearnedPattern, WorkflowEvent
from app.services.smart_outreach import OutreachProfile, OutreachTouch
from app.services.outreach_intelligence.models import (
    OutreachEvent,
    OutreachExperienceAggregate,
    OutreachOutcomeCorrection,
)
from app.services.outreach_intelligence.event_ledger import (
    record_event,
    ensure_outreach_intelligence_schema,
)
from app.services.outreach_intelligence.outcomes import (
    classify_email_reply,
    extract_call_commercial_signals,
    outcome_level,
)
from app.services.outreach_intelligence.experience_store import (
    build_lead_outreach_profile,
    retrieve_similar_lead_experience,
    MIN_EVIDENCE,
)
from app.services.outreach_intelligence.learning_loop import (
    on_outreach_event,
    recalculate_segment_stats,
    apply_correction,
)
from app.services.outreach_intelligence.report import build_intelligence_report
from conftest import memory_engine


def _db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    ensure_outreach_intelligence_schema(engine)
    return engine, sessionmaker(bind=engine)()


def _lead(db, company, category="CAFE", **kw):
    lead = B2BLead(
        company=company,
        email=kw.pop("email", None),
        phone=kw.pop("phone", None),
        city=kw.pop("city", "Bengaluru"),
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


def test_event_append_and_idempotency_no_overwrite():
    engine, db = _db()
    try:
        lead = _lead(db, "Idem Cafe", email="a@example.com")
        a = record_event(
            db, event_type="EMAIL_SENT", lead_id=lead.id, channel="email",
            delivery_status="SENT", confidence="OBSERVED",
            idempotency_key="email-sent-1", outcome="SENT",
        )
        db.commit()
        b = record_event(
            db, event_type="EMAIL_SENT", lead_id=lead.id, channel="email",
            delivery_status="DELIVERED", confidence="OBSERVED",
            idempotency_key="email-sent-1", outcome="DELIVERED",
        )
        db.commit()
        assert a.id == b.id
        assert b.delivery_status == "SENT"
        assert b.outcome == "SENT"
        assert db.query(OutreachEvent).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_email_and_call_classification_and_levels():
    email = classify_email_reply(intent="SAMPLE_REQUESTED")
    assert email["class"] == "SAMPLE_REQUEST"
    assert email["confidence"] == "OBSERVED"
    assert email["level"] >= 5

    bounce = classify_email_reply(event_type="EMAIL_BOUNCED")
    assert bounce["class"] == "BOUNCE"

    call = extract_call_commercial_signals(outcome="SEND_PRICING")
    assert call["signals"]["price_discussed"] is True
    assert call["confidence"] == "OBSERVED"
    assert call["level"] >= 5

    assert outcome_level(channel="email", delivery_status="SENT") == 0
    assert outcome_level(channel="email", email_class="POSITIVE") == 4
    assert outcome_level(channel="call", call_outcome="MEETING_REQUESTED") == 6


def test_experience_retrieval_respects_min_evidence():
    engine, db = _db()
    try:
        db.add(OutreachExperienceAggregate(
            segment_key="CAFE", channel="email", metric="positive_reply_rate",
            value=0.5, sample_size=2, wins=1,
        ))
        db.commit()
        xp = retrieve_similar_lead_experience(db, category="CAFE", channel="email")
        assert xp["metrics"]["positive_reply_rate"]["promoted"] is False
        assert xp["min_evidence"] == MIN_EVIDENCE

        row = db.query(OutreachExperienceAggregate).first()
        row.sample_size = MIN_EVIDENCE
        row.wins = 3
        row.value = 0.6
        db.commit()
        xp2 = retrieve_similar_lead_experience(db, category="CAFE", channel="email")
        assert xp2["metrics"]["positive_reply_rate"]["promoted"] is True
    finally:
        db.close()
        engine.dispose()


def test_learning_update_from_observed_fixtures():
    engine, db = _db()
    try:
        lead = _lead(db, "Learn Cafe", email="l@example.com", phone="9876500001")
        ev = record_event(
            db, event_type="EMAIL_REPLY_RECEIVED", lead_id=lead.id, channel="email",
            outcome="SAMPLE_REQUESTED", confidence="OBSERVED",
            response_status="REPLIED", idempotency_key="reply-1",
        )
        db.commit()
        result = on_outreach_event(db, ev)
        db.commit()
        assert result["updated_aggregates"] is True
        assert db.query(OutreachExperienceAggregate).count() >= 1

        before = db.query(OutreachExperienceAggregate).count()
        ev2 = record_event(
            db, event_type="EMAIL_REPLY_RECEIVED", lead_id=lead.id, channel="email",
            outcome="INTERESTED", confidence="INFERRED", idempotency_key="reply-inferred",
        )
        db.commit()
        on_outreach_event(db, ev2)
        db.commit()
        assert db.query(OutreachExperienceAggregate).count() == before
    finally:
        db.close()
        engine.dispose()


def test_correction_write():
    engine, db = _db()
    try:
        lead = _lead(db, "Correct Cafe", email="c@example.com")
        row = apply_correction(
            db, lead_id=lead.id, field="outcome",
            old_value="POSITIVE", new_value="NOT_RELEVANT", note="human review",
        )
        db.commit()
        assert row.id is not None
        assert db.query(OutreachOutcomeCorrection).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_report_shape():
    engine, db = _db()
    try:
        lead = _lead(db, "Report Cafe", email="r@example.com")
        record_event(
            db, event_type="EMAIL_SENT", lead_id=lead.id, channel="email",
            delivery_status="SENT", confidence="OBSERVED",
        )
        record_event(
            db, event_type="CALL_OUTCOME_RECORDED", lead_id=lead.id, channel="call",
            outcome="INTERESTED", confidence="OBSERVED",
        )
        db.commit()
        report = build_intelligence_report(db)
        assert "email_funnel" in report
        assert "call_funnel" in report
        assert "what_we_learned" in report
        assert "known_limitations" in report
        assert report["safety"]["does_not_send"] is True
        assert report["email_funnel"]["sent"] >= 1
        assert report["call_funnel"]["dialled"] >= 1
        assert "delivered" in report["email_funnel"]
        assert "positive" in report["email_funnel"]
    finally:
        db.close()
        engine.dispose()


def test_learning_never_returns_eligibility_or_suppress_keys():
    engine, db = _db()
    try:
        lead = _lead(db, "Safe Cafe", email="s@example.com", phone="9876500099")
        db.commit()
        profile = build_lead_outreach_profile(db, lead)
        blob = str(profile).lower()
        assert "eligible" not in profile
        assert "suppress" not in profile
        assert "eligibility" not in blob
        assert profile["channels"]["selected"] is None
        assert "best_channel" not in profile
    finally:
        db.close()
        engine.dispose()


def test_profile_recommends_cta_without_choosing_single_channel():
    engine, db = _db()
    try:
        current = _lead(db, "Next Cafe", email="next@example.com", phone="9876500999")
        db.commit()
        profile = build_lead_outreach_profile(db, current)
        assert "recommended_cta" in profile
        assert "email" in profile["channels"]
        assert "call" in profile["channels"]
        assert profile["channels"].get("selected") is None
        assert "best_channel" not in profile
    finally:
        db.close()
        engine.dispose()


def test_recalculate_is_dry():
    engine, db = _db()
    try:
        lead = _lead(db, "Dry Cafe", email="d@example.com")
        record_event(
            db, event_type="EMAIL_REPLY_RECEIVED", lead_id=lead.id,
            channel="email", outcome="INTERESTED", confidence="OBSERVED",
        )
        db.commit()
        result = recalculate_segment_stats(db)
        assert "no outreach sent" in result["note"]
        assert result.get("sent") is None or result.get("sent") is False
    finally:
        db.close()
        engine.dispose()
