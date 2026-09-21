"""One calling authority, one execution choke point, no email-side veto.

The production-readiness contract:

  * founder_call_pipeline.may_place_ai_call is the per-lead permission gate
  * voice_router.place_call is the only dial path and reads AI_CALLING_ENABLED
  * calling_agent.trigger_vapi_call cannot skip either
  * evaluate_next_action is email permission and must not suppress a call
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base
from app.services import founder_call_pipeline as pipeline
from app.services import outreach_orchestrator as o
from app.services import preference_registry as pref
from app.services import voice_router
from app.services.calling_agent import CallingAgentService
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
    f.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None
    return f


def _lead(db, **kw):
    kw.setdefault("company", "Authority Cafe")
    kw.setdefault("segment", "cafe")
    kw.setdefault("phone", "9876543210")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


def _arm(monkeypatch):
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "configured"))
    monkeypatch.setattr(voice_router, "active", lambda: "nuraveda")


def test_flag_off_blocks_trigger_even_with_a_patched_dialler(db, registry, monkeypatch):
    monkeypatch.setenv("AI_CALLING_ENABLED", "0")
    placed = []
    monkeypatch.setattr(voice_router, "place_call",
                        lambda *a, **k: placed.append(1) or type("R", (), {"placed": True, "error": "", "provider_call_id": "x"})())
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "configured"))
    lead = _lead(db)
    ok, why = CallingAgentService.trigger_vapi_call(db, lead)
    assert ok is False
    assert "AI_CALLING_ENABLED" in why
    assert placed == []


def test_kill_switch_beats_the_arming_flag(db, registry, monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setenv("AI_CALLING_KILL_SWITCH", "1")
    lead = _lead(db)
    ok, why = CallingAgentService.trigger_vapi_call(db, lead)
    assert ok is False
    assert "KILL_SWITCH" in why


def test_check_eligibility_delegates_to_may_place(db, registry, monkeypatch):
    _arm(monkeypatch)
    lead = _lead(db, do_not_call=True)
    ok, why = CallingAgentService.check_eligibility(db, lead)
    assert ok is False
    assert "do_not_call" in why
    assert pipeline.may_place_ai_call(lead)[0] is False


def test_run_cycle_still_calls_when_email_is_draft_only(db, registry, monkeypatch):
    """DRAFT_ONLY on email must not drop a callable lead from the cycle."""
    from app.services.smart_outreach import run_cycle
    from app.services.trust_promoter import DISCOVERED

    _arm(monkeypatch)
    calls = []

    class Result:
        placed = True
        error = ""
        provider_call_id = "cycle-call"

    monkeypatch.setattr(voice_router, "place_call",
                        lambda lead, **k: calls.append(lead.id) or Result())

    _lead(db, company="Draft Only Cafe", phone="9876543210",
          email="info@draft.example", email_trust=DISCOVERED,
          email_confidence=10, estimated_value=0,
          stage_entered_date=datetime.utcnow())

    result = run_cycle(db, limit=5)
    assert calls, result
    assert result.get("calls_placed", 0) >= 1 or any(
        r.get("call", {}).get("placed") for r in result["results"]
    )


def test_whatsapp_is_not_in_the_day0_pair():
    assert o.WHATSAPP not in o.DAY0_CHANNELS
    assert all(ch != o.WHATSAPP for _, ch, _ in o.SEQUENCE)
