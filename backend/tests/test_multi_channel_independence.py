"""CALL and EMAIL are evaluated independently; BOTH when both eligible."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base
from app.services import outreach_orchestrator as o
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
    from app.services import preference_registry as pref
    f = tmp_path / "dnd.txt"
    f.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None
    return f


@pytest.fixture
def voice_ready(monkeypatch):
    from app.services import voice_router
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setattr(voice_router, "config_status",
                        lambda: (True, "nuraveda ready (test fixture)"))


def _lead(db, **kw):
    kw.setdefault("company", "Independence Cafe")
    kw.setdefault("segment", "cafe")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


def test_both_when_email_and_phone_eligible(db, registry, voice_ready):
    from app.services.trust_promoter import VERIFIED
    lead = _lead(db, phone="9876543210", email="owner@cafe.example",
                 email_trust=VERIFIED, email_confidence=80,
                 stage_entered_date=datetime.utcnow())
    plan = o.plan_channels(lead, db)
    assert plan["kind"] == "BOTH"
    assert set(plan["channels"]) == {o.EMAIL, o.PHONE}


def test_email_failure_does_not_suppress_call(db, registry, voice_ready):
    from app.services.trust_promoter import DISCOVERED
    lead = _lead(db, phone="9876543210", email="info@draft.example",
                 email_trust=DISCOVERED, email_confidence=10,
                 stage_entered_date=datetime.utcnow())
    plan = o.plan_channels(lead, db)
    assert o.PHONE in plan["channels"]
    assert o.EMAIL not in plan["channels"]
    assert plan["kind"] == "CALL"


def test_whatsapp_stays_isolated_by_default(db, registry, voice_ready, monkeypatch):
    monkeypatch.delenv("AISENSY_ENABLED", raising=False)
    lead = _lead(db, phone="9876543210", whatsapp_number="9876543210",
                 consent_status="EXPLICIT", stage_entered_date=datetime.utcnow())
    plan = o.plan_channels(lead, db)
    assert o.WHATSAPP not in plan.get("channels", [])
    elig = o.eligibility(lead, db)
    assert elig[o.WHATSAPP]["eligible"] is False
    assert "AISENSY_ENABLED" in elig[o.WHATSAPP]["reason"]


def test_day0_does_not_choose_best_channel():
    assert o.DAY0_CHANNELS == (o.EMAIL, o.PHONE)
    assert o.WHATSAPP not in o.DAY0_CHANNELS
