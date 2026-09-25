from datetime import datetime, timedelta

from app.models.models import AgentLog
from app.services.call_learning import build_strategy_hints, record_call_learning


def test_call_learning_is_advisory_and_aggregates_repeated_patterns(db_session, lead_factory):
    lead = lead_factory()
    record_call_learning(
        db_session, lead,
        outcome="NOT_INTERESTED",
        summary="Already have supplier",
        details={"objection_reason": "already have supplier"},
    )
    record_call_learning(
        db_session, lead,
        outcome="BUSY",
        callback_window="7pm",
        details={"objection_reason": "busy"},
    )
    db_session.commit()

    hints = build_strategy_hints(db_session)
    assert hints["mode"] == "ADVISORY_ONLY"
    assert hints["observations"] == 2
    assert ("NOT_INTERESTED", 1) in hints["outcomes"]
    assert any("already have supplier" in item[0] for item in hints["objections"])
    assert any("7pm" in item[0] for item in hints["callback_windows"])
    assert "OPT_OUT" in hints["terminal_outcomes_never_retry"]


def test_learning_does_not_create_contact_permission(db_session, lead_factory):
    lead = lead_factory()
    before = lead.consent_status
    record_call_learning(
        db_session, lead,
        outcome="INTERESTED",
        summary="Interested in coffee",
        details={"preferred_channel": "WHATSAPP"},
    )
    db_session.commit()

    row = db_session.query(AgentLog).filter_by(
        agent_name="ai_call_learning",
        action="CALL_LEARNING_RECORDED",
    ).one()
    assert row.payload["outcome"] == "INTERESTED"
    assert row.payload["details"]["preferred_channel"] == "WHATSAPP"
    assert lead.consent_status == before
