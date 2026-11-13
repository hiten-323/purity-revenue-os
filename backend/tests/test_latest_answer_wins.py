"""17 Oct: outcome reporting never drops an outcome on an illegal stage move.

Latest clear answer wins: an explicit NOT_INTERESTED downgrades interest,
OPT_OUT always wins (and sets DNC), stray engine FAILED after a real answer
keeps the stage, and an unevidenced upgrade never creates WhatsApp consent.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, WorkflowEvent
from app.services import founder_call_pipeline as p
from app.services import preference_registry as pref
from conftest import memory_engine


@pytest.fixture
def db(tmp_path, monkeypatch):
    f = tmp_path / "dnd.txt"
    f.write_text("# scrub\n", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


def _lead(db, **kw):
    lead = B2BLead(company=kw.pop("company", "Latest Answer Traders"),
                   phone=kw.pop("phone", "9876501234"), segment="distributor", **kw)
    db.add(lead)
    db.commit()
    return lead


def test_not_interested_after_interest_downgrades_and_saves(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", summary="wants catalogue")
    db.commit()
    assert p.stage_of(lead) == p.AI_INTEREST_DETECTED
    p.record_ai_outcome(lead, db, "NOT_INTERESTED", summary="actually no")
    db.commit()
    assert p.stage_of(lead) == p.AI_NOT_INTERESTED
    assert lead.call_outcome_last == "NOT_INTERESTED"
    assert lead.ai_retry_after is None


def test_not_interested_after_founder_call_requested_downgrades(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED")
    p.advance(lead, db, p.FOUNDER_CALL_REQUESTED)
    db.commit()
    p.record_ai_outcome(lead, db, "NOT_INTERESTED")
    assert p.stage_of(lead) == p.AI_NOT_INTERESTED


def test_failed_after_interest_keeps_stage_without_raising(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED")
    db.commit()
    p.record_ai_outcome(lead, db, "FAILED")
    assert p.stage_of(lead) == p.AI_INTEREST_DETECTED
    assert lead.call_outcome_last == "FAILED"


def test_opt_out_always_wins_and_sets_dnc(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "WRONG_NUMBER")
    db.commit()
    p.record_ai_outcome(lead, db, "OPT_OUT")
    assert p.stage_of(lead) == p.AI_OPTED_OUT
    assert lead.do_not_call is True


def test_nothing_moves_an_opted_out_lead(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "OPT_OUT")
    db.commit()
    p.record_ai_outcome(lead, db, "INTERESTED", call_meta={"call_ref": "r2"})
    assert p.stage_of(lead) == p.AI_OPTED_OUT
    assert lead.do_not_call is True


def test_unevidenced_whatsapp_after_not_interested_is_kept_and_unbound(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "NOT_INTERESTED")
    db.commit()
    p.record_ai_outcome(lead, db, "WHATSAPP_OPT_IN", summary="send on whatsapp")
    db.commit()
    assert p.stage_of(lead) == p.AI_NOT_INTERESTED
    assert (lead.consent_status or "UNKNOWN").upper() == "UNKNOWN"
    assert db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type == "WHATSAPP_CONSENT_UNBOUND").count() == 1


def test_evidenced_interest_after_not_interested_upgrades(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "NOT_INTERESTED")
    db.commit()
    p.record_ai_outcome(lead, db, "INTERESTED", call_meta={"call_ref": "same-call"})
    assert p.stage_of(lead) == p.AI_INTEREST_DETECTED


def test_real_answer_after_engine_no_answer_race_is_applied(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "FAILED")
    db.commit()
    assert p.stage_of(lead) == p.AI_NO_ANSWER
    p.record_ai_outcome(lead, db, "INTERESTED", call_meta={"call_ref": "c1"})
    assert p.stage_of(lead) == p.AI_INTEREST_DETECTED
