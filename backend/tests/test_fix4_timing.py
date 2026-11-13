"""fix4: call timing reaches call_results; timing-only update; KPI GET read-only."""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base
from conftest import memory_engine
from app.api.founder_router import (post_ai_call_outcome, AICallOutcomeBody,
                                    post_ai_call_timing, AICallTimingBody)


def _db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_outcome_report_saves_answered_duration_and_turns():
    from app.services.call_intelligence.capture import find_result
    db = _db()
    lead = B2BLead(company="T Cafe", phone="9876500001", segment="cafe")
    db.add(lead); db.commit()
    turns = [{"role": "assistant", "text": "Namaste", "turn_index": 0},
             {"role": "user", "text": "haan boliye", "turn_index": 1},
             {"role": "assistant", "text": "coffee?", "turn_index": 2},
             {"role": "user", "text": "nahi chahiye", "turn_index": 3}]
    post_ai_call_outcome(AICallOutcomeBody(
        lead_id=lead.id, outcome="NOT_INTERESTED", summary="no", call_ref="ppp-%d-aa" % lead.id,
        answered_at="2026-10-14T09:00:00Z", ended_at="2026-10-14T09:00:37Z", turns=turns), db)
    row = find_result(db, "ppp-%d-aa" % lead.id)
    assert row is not None and row.answered_at is not None
    assert abs(row.duration_seconds - 37) < 1
    assert row.user_turn_count == 2

    # hangup later than the mid-call tool report -> duration extended
    out = post_ai_call_timing(AICallTimingBody(
        lead_id=lead.id, call_ref="ppp-%d-aa" % lead.id,
        answered_at="2026-10-14T09:00:00Z", ended_at="2026-10-14T09:00:52Z", turns=turns), db)
    assert abs(out["duration_seconds"] - 52) < 1
    db.refresh(row)
    assert row.outcome and row.status == "FINAL"


def test_kpis_get_does_not_write():
    from app.services.crm_tracker import CRMTrackerService
    db = _db()
    db.add(B2BLead(company="Old", phone="9876500002", segment="cafe", status="EMAIL_SENT", email_opens=0))
    db.commit()
    db.commit = lambda: (_ for _ in ()).throw(AssertionError("GET committed"))
    CRMTrackerService.get_kpis(db)
