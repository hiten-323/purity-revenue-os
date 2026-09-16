from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, WorkflowEvent
from app.services import smart_outreach as so
from conftest import memory_engine


def test_execute_one_requires_current_founder_approval(monkeypatch):
    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Approval Gate Test", email="buyer@example.com")
        db.add(lead)
        db.commit()

        called = {"send": False}

        def forbidden(*args, **kwargs):
            called["send"] = True
            raise AssertionError("provider must not be reached without founder approval")

        monkeypatch.setattr(so, "classify_lead", forbidden)

        result = so.execute_one(db, lead)

        assert result["status"] == "SKIPPED"
        assert result["action"] == "FOUNDER_REVIEW"
        assert "founder approval" in result["reason"]
        assert called["send"] is False
    finally:
        db.close()
        engine.dispose()


def test_execute_one_accepts_fresh_unconsumed_approval(monkeypatch):
    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Approval Gate Test", email="buyer@example.com")
        db.add(lead)
        db.commit()
        db.add(WorkflowEvent(
            lead_id=lead.id,
            event_type="OUTREACH_APPROVED",
            actor="FOUNDER",
            channel="approval",
            payload={"to": lead.email, "approved_at": datetime.utcnow().isoformat()},
            occurred_at=datetime.utcnow(),
        ))
        db.commit()

        monkeypatch.setattr(so, "classify_lead", lambda db, lead: type("P", (), {
            "id": None, "intent": "BOUNCED", "warmth": "COOLDOWN", "last_channel": None,
        })())

        result = so.execute_one(db, lead)
        assert result["status"] == "SKIPPED"
        assert result["action"] == "COOLDOWN"
    finally:
        db.close()
        engine.dispose()


def test_expired_approval_does_not_authorize_execution():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Approval Gate Test", email="buyer@example.com")
        db.add(lead)
        db.commit()
        db.add(WorkflowEvent(
            lead_id=lead.id,
            event_type="OUTREACH_APPROVED",
            actor="FOUNDER",
            channel="approval",
            payload={"to": lead.email},
            occurred_at=datetime.utcnow() - timedelta(days=8),
        ))
        db.commit()

        assert so._has_pending_founder_approval(db, lead.id) is False
    finally:
        db.close()
        engine.dispose()
