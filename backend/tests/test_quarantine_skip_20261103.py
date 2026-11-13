"""3 Nov 2026 (Hiten: "pause, fix, resume"): QUARANTINED shared-directory leads
never enter the AI dial queue. Pure selector tests on an in-memory DB; never dials."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ["AI_CALLING_ENABLED"] = "0"
os.environ["AUTO_AI_CALLING_ENABLED"] = "0"

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base
from app.services import founder_call_pipeline as p
from app.services import preference_registry as pref
from conftest import memory_engine


@pytest.fixture
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    f = tmp_path / "dnd.txt"
    f.write_text("# empty\n", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None
    return f


def _lead(db, company, trust, phone):
    l = B2BLead(company=company, phone=phone, segment="horeca", phone_trust=trust)
    db.add(l)
    db.commit()
    return l


def _retry(db, company, trust, phone):
    l = _lead(db, company, trust, phone)
    l.outreach_stage = p.AI_NO_ANSWER
    l.ai_call_count = 1
    l.last_call_date = datetime.utcnow() - timedelta(hours=170)
    l.ai_retry_after = datetime.utcnow() - timedelta(hours=1)
    l.call_outcome_last = "NO_ANSWER"
    db.commit()
    return l


def _callback(db, company, trust, phone):
    l = _retry(db, company, trust, phone)
    l.callback_retry_count = 1
    l.founder_callback_window = "2026-11-02 10:30:00"
    db.commit()
    return l


def test_quarantined_cold_retry_and_callback_leads_are_never_candidates(db, registry, monkeypatch):
    from tests.cohort_fixture import admit
    from app.services import ai_call_cycle, outbound_cohort
    cold = _lead(db, "Quarantined Cold", "QUARANTINED", "9814012301")
    retry = _retry(db, "Quarantined Retry", "QUARANTINED", "9814012302")
    callback = _callback(db, "Quarantined Callback", "QUARANTINED", "9814012303")
    clean = _lead(db, "Clean Cold", "UNKNOWN", "9814012304")
    # Preserve the quarantine state; test the selector's filter independently
    # from the separate phone-verification and call-authorization gates.
    admit(monkeypatch, db, [cold, retry, callback, clean], calls=False)
    monkeypatch.setattr(outbound_cohort, 'call_refusal', lambda *a, **k: None)
    names = [c.company for c in ai_call_cycle.select_call_candidates(db, limit=50)]
    assert "Clean Cold" in names
    assert not any(n.startswith("Quarantined") for n in names), names


def test_other_trust_values_still_dialable(db, registry, monkeypatch):
    from tests.cohort_fixture import admit
    from app.services import ai_call_cycle
    lead = _lead(db, "Discovered Cold", "DISCOVERED", "9814012305")
    admit(monkeypatch, db, [lead], calls=True)
    names = [c.company for c in ai_call_cycle.select_call_candidates(db, limit=50)]
    assert "Discovered Cold" in names


def test_existing_blocked_trusts_still_excluded(db, registry, monkeypatch):
    from tests.cohort_fixture import admit
    from app.services import ai_call_cycle, outbound_cohort
    leads = []
    for i, t in enumerate(("INVALID", "SHARED_QUARANTINE", "SIP_INVALID", "DEAD_SERIES", "QUARANTINED")):
        leads.append(_lead(db, "Blocked " + t, t, "981401236" + str(i)))
    # Keep each blocked trust value intact while isolating the selector filter.
    admit(monkeypatch, db, leads, calls=False)
    monkeypatch.setattr(outbound_cohort, 'call_refusal', lambda *a, **k: None)
    names = [c.company for c in ai_call_cycle.select_call_candidates(db, limit=50)]
    assert not any(n.startswith("Blocked") for n in names), names
