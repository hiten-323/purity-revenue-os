from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.models.models import AgentLog, B2BLead
from app.services.call_learning import build_strategy_hints, record_call_learning


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _lead():
    return B2BLead(id=990001, company="Learning Test Co", phone="919855599999",
                   segment="cafe", consent_status="UNKNOWN")


def test_call_learning_is_advisory_and_aggregates_repeated_patterns():
    db = _session()
    try:
        lead = _lead()
        db.add(lead)
        db.flush()
        record_call_learning(db, lead, outcome="NOT_INTERESTED",
                              summary="Already have supplier",
                              details={"objection_reason": "already have supplier"})
        record_call_learning(db, lead, outcome="BUSY", callback_window="7pm",
                              details={"objection_reason": "busy"})
        db.commit()
        hints = build_strategy_hints(db)
        assert hints["mode"] == "ADVISORY_ONLY"
        assert hints["observations"] == 2
        assert ("NOT_INTERESTED", 1) in hints["outcomes"]
        assert any("already have supplier" in item[0] for item in hints["objections"])
        assert any("7pm" in item[0] for item in hints["callback_windows"])
        assert "OPT_OUT" in hints["terminal_outcomes_never_retry"]
    finally:
        db.close()


def test_learning_does_not_create_contact_permission():
    db = _session()
    try:
        lead = _lead()
        db.add(lead)
        db.flush()
        before = lead.consent_status
        record_call_learning(
            db, lead, outcome="INTERESTED",
            summary="Interested in coffee",
            details={"preferred_channel": "WHATSAPP"},
        )
        db.commit()
        row = db.query(AgentLog).filter_by(
            agent_name="ai_call_learning",
            action="CALL_LEARNING_RECORDED",
        ).one()
        assert row.payload["outcome"] == "INTERESTED"
        assert row.payload["details"]["preferred_channel"] == "WHATSAPP"
        assert lead.consent_status == before
    finally:
        db.close()
