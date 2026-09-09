"""
An assessment is not an outcome, and a journal that conflates them lies.

Every step now writes to the business's own record. That is only useful if two
things hold:

  1. "We concluded no channel can reach this account" and "we emailed this
     account and the server accepted it" are counted separately. Otherwise a
     system that has contacted nobody reports 1,858 recorded steps and reads
     as busy. This codebase has produced that exact illusion before -- the
     tender agents' hardcoded pipeline, the "3 email ready" with zero sendable.

  2. The remark is what the deciding code actually said. A journal that
     improves on the gate's wording will be believed, and then trusted, and
     then wrong.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, LeadInteraction
from app.services import lead_journal as journal
from conftest import memory_engine


@pytest.fixture
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


def _lead(db, company="Test Cafe"):
    lead = B2BLead(company=company)
    db.add(lead)
    db.commit()
    return lead


# ------------------------------------- outcomes are not assessments --

def test_an_assessment_is_not_an_outcome():
    assert journal.is_outcome(journal.SENT) is True
    assert journal.is_outcome(journal.BLOCKED) is True
    assert journal.is_outcome("INTERESTED") is True

    assert journal.is_outcome(journal.QUEUED) is False
    assert journal.is_outcome(journal.NO_CHANNEL) is False
    assert journal.is_outcome(journal.SKIPPED) is False


def test_the_two_sets_do_not_overlap():
    assert not (journal.OUTCOMES & journal.ASSESSMENTS)


def test_summary_reports_them_separately(db):
    a, b = _lead(db, "A"), _lead(db, "B")
    journal.record(a, db, method=journal.EMAIL, outcome=journal.SENT,
                   remark="server accepted it")
    journal.record(b, db, method=journal.ORCHESTRATOR,
                   outcome=journal.NO_CHANNEL, remark="nothing can reach them")
    db.commit()

    s = journal.summary(db)
    assert s["entries"] == 2
    assert s["real_outcomes"] == 1
    assert s["assessments"] == 1
    assert s["businesses_with_an_outcome"] == 1, (
        "an assessment must not count as a business we have reached")


def test_a_thousand_assessments_are_still_zero_outcomes(db):
    """The illusion this guards against, stated directly."""
    for i in range(50):
        lead = _lead(db, f"Business {i}")
        journal.record(lead, db, method=journal.ORCHESTRATOR,
                       outcome=journal.NO_CHANNEL, remark="no channel")
    db.commit()

    s = journal.summary(db)
    assert s["entries"] == 50
    assert s["real_outcomes"] == 0
    assert s["businesses_with_an_outcome"] == 0


# --------------------------------------------------- the remark is verbatim --

def test_the_remark_is_stored_as_given(db):
    lead = _lead(db)
    gate_said = ("VALIDATED — may draft, not send")
    journal.record(lead, db, method=journal.EMAIL, outcome=journal.BLOCKED,
                   remark=gate_said)
    db.commit()

    row = db.query(LeadInteraction).filter_by(lead_id=lead.id).one()
    assert row.remark == gate_said


def test_who_recorded_it_is_kept(db):
    lead = _lead(db)
    journal.record(lead, db, method=journal.PHONE, outcome="NO_ANSWER",
                   remark="rang out", by="ai_voice_agent")
    db.commit()
    assert db.query(LeadInteraction).filter_by(lead_id=lead.id).one().created_by \
        == "ai_voice_agent"


# ------------------------------------------------------------- deduping --

def test_an_unchanged_conclusion_is_not_recorded_twice(db):
    """A nightly sweep over 1,858 businesses must not add 1,858 rows a night."""
    lead = _lead(db)
    first = journal.record(lead, db, method=journal.ORCHESTRATOR,
                           outcome=journal.NO_CHANNEL, remark="no channel")
    db.commit()
    second = journal.record(lead, db, method=journal.ORCHESTRATOR,
                            outcome=journal.NO_CHANNEL, remark="no channel")
    db.commit()

    assert first is not None
    assert second is None
    assert db.query(LeadInteraction).filter_by(lead_id=lead.id).count() == 1


def test_a_changed_conclusion_is_recorded(db):
    lead = _lead(db)
    journal.record(lead, db, method=journal.ORCHESTRATOR,
                   outcome=journal.NO_CHANNEL, remark="no channel")
    db.commit()
    journal.record(lead, db, method=journal.ORCHESTRATOR,
                   outcome=journal.QUEUED, remark="email is now eligible")
    db.commit()
    assert db.query(LeadInteraction).filter_by(lead_id=lead.id).count() == 2


def test_force_records_a_genuine_repeat(db):
    """A second delivery failure to the same address is not the same fact as
    the first."""
    lead = _lead(db)
    for _ in range(2):
        journal.record(lead, db, method=journal.EMAIL, outcome=journal.BLOCKED,
                       remark="mailbox full", force=True)
    db.commit()
    assert db.query(LeadInteraction).filter_by(lead_id=lead.id).count() == 2


def test_dedupe_only_looks_at_the_most_recent(db):
    """A -> B -> A is three real events, not two."""
    lead = _lead(db)
    for outcome in (journal.NO_CHANNEL, journal.QUEUED, journal.NO_CHANNEL):
        journal.record(lead, db, method=journal.ORCHESTRATOR, outcome=outcome,
                       remark=outcome)
        db.commit()
    assert db.query(LeadInteraction).filter_by(lead_id=lead.id).count() == 3


# ------------------------------------------------------- it never breaks --

def test_a_journal_failure_does_not_raise(db):
    """Observability that can break the thing it observes is a liability."""
    assert journal.record(None, db, method="x", outcome="y", remark="z") is None


def test_history_is_oldest_first(db):
    lead = _lead(db)
    for i, o in enumerate((journal.SENT, journal.BLOCKED, journal.SENT)):
        r = journal.record(lead, db, method=journal.EMAIL, outcome=o,
                           remark=f"step {i}", force=True)
        r.occurred_at = datetime(2026, 1, i + 1)
    db.commit()

    rows = journal.history(db, lead.id)
    assert [r["remark"] for r in rows] == ["step 0", "step 1", "step 2"]


def test_history_does_not_default_unknown_fields(db):
    """'no supplier recorded' and 'they have no supplier' are different facts."""
    lead = _lead(db)
    journal.record(lead, db, method=journal.EMAIL, outcome=journal.SENT,
                   remark="sent")
    db.commit()

    row = journal.history(db, lead.id)[0]
    assert row["current_supplier"] is None
    assert row["decision_maker"] is None
