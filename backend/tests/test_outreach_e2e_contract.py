"""End-to-end outreach safety contracts.

These tests exercise the decision authority, cadence state, smart-outreach
planner, and API wiring together against an isolated database. Providers are
not contacted: this is a dry run of the decision path only.
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from conftest import memory_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.models import Base, B2BLead, WorkflowEvent
from app.services.decision_engine import evaluate_next_action
from app.services.sequence_engine import state
from app.services.smart_outreach import classify_lead, plan_touch


@pytest.fixture
def db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def lead(db, **kw):
    defaults = dict(
        company="E2E Cafe",
        city="Abohar",
        division="CAFE",
        email="owner@example.com",
        email_trust="VERIFIED",
        email_confidence=80,
        email_verification_status="VALID",
        website="https://example.com",
        maps_rating=4.5,
        coffee_buying_score=70,
        status="DISCOVERED",
        contact_status="CONTACTABLE",
    )
    defaults.update(kw)
    obj = B2BLead(**defaults)
    # The production quality gate requires explicit fit evidence. Keep that
    # requirement intact and make this fixture represent a known-fit prospect.
    obj.coffee_buying_score = 80
    db.add(obj)
    db.commit()
    return obj


def event(db, l, event_type, payload=None, when=None):
    db.add(WorkflowEvent(
        lead_id=l.id,
        event_type=event_type,
        actor="E2E",
        channel="email",
        payload=payload or {},
        occurred_at=when or datetime.utcnow(),
    ))
    db.commit()


def test_cold_verified_contact_can_reach_send_decision(db):
    l = lead(db)
    d = evaluate_next_action(l, db)
    assert d["action"] == "SEND", d
    assert d["blockers"] == [], d


def test_opt_out_is_absolute_across_authority_and_planner(db):
    l = lead(db)
    event(db, l, "UNSUBSCRIBED")
    d = evaluate_next_action(l, db)
    assert d["action"] == "SUPPRESS", d
    p = plan_touch(db, l)
    assert p["execute"] is False, p
    assert p["action"] == "COOLDOWN", p


def test_reply_stops_scheduled_outreach(db):
    l = lead(db)
    event(db, l, "EMAIL_REPLY_RECEIVED", {"intents": ["INTERESTED"], "polarity": "positive", "next_action": "respond", "sla_minutes": 60, "confidence": 95})
    d = evaluate_next_action(l, db)
    assert d["action"] in {"DRAFT_ONLY", "FOUNDER_REVIEW"}, d
    st = state(l, db)
    assert st["active"] is False
    assert st["reason"] == "replied"


def test_unverified_contact_never_becomes_send(db):
    l = lead(db, email_trust="DISCOVERED", email_confidence=20)
    d = evaluate_next_action(l, db)
    assert d["action"] != "SEND", d
    assert "not_sendable" in d["blockers"] or d["action"] in {"DRAFT_ONLY", "ENRICH", "FOUNDER_REVIEW"}, d


def test_sequence_waits_before_followup(db):
    l = lead(db)
    event(db, l, "EMAIL_SENT", {"to": l.email, "message_id": "e2e-1"})
    st = state(l, db)
    assert st["active"] is True
    assert st["next_touch"] == "nudge"
    assert st["ready"] is False
    d = evaluate_next_action(l, db)
    assert d["action"] == "WAIT", d


def test_completed_sequence_does_not_restart(db):
    l = lead(db)
    first = datetime.utcnow() - timedelta(days=30)
    for i in range(5):
        event(db, l, "EMAIL_SENT", {"to": l.email, "message_id": f"e2e-{i}"}, first + timedelta(days=i * 3))
    st = state(l, db)
    assert st["active"] is False
    assert st["reason"] == "completed"
    d = evaluate_next_action(l, db)
    assert d["action"] == "NONE", d


def test_smart_outreach_router_uses_live_decision_authority():
    from app.api.smart_outreach_router import evaluate_next_action as imported, next_action
    from app.services.decision_engine import evaluate_next_action as authority
    assert imported is authority
    assert callable(next_action)


def test_classification_is_not_a_send_permission(db):
    l = lead(db, email_trust="DISCOVERED", email_confidence=20)
    profile = classify_lead(db, l)
    assert profile.category == "CAFE"
    d = evaluate_next_action(l, db)
    assert d["action"] != "SEND"


def test_calling_agent_has_no_direct_vapi_execution():
    from app.services.calling_agent import CallingAgentService

    source = inspect.getsource(CallingAgentService.trigger_vapi_call)
    assert "api.vapi.ai" not in source
    assert "VAPI_API_KEY" not in source
    assert "urllib.request" not in source
    assert "voice_router" in source
    assert "_place_qualification_call" in source
    assert "_place_consented_call" not in source


# ---- email/WhatsApp consent is not consent to be called --------------------
#
# consent_status EXPLICIT is only ever recorded for email or WhatsApp. It used
# to unlock a "consented call" path that skipped the DND registry scrub and the
# one-call rule, so a prospect who asked for details on WhatsApp could be
# called up to three times without either. These pin that every AI call now
# runs under founder_call_pipeline.may_place_ai_call, whatever the consent.

def _dry_voice(monkeypatch):
    from app.services import voice_router

    class Result:
        placed = True
        error = None
        provider_call_id = "dry-run-provider-call"

    calls = []

    def fake_place_call(lead_obj, context=None, scheduled_at=None):
        calls.append((lead_obj.id, context))
        return Result()

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "configured"))
    monkeypatch.setattr(voice_router, "active", lambda: "nuraveda")
    monkeypatch.setattr(voice_router, "place_call", fake_place_call)
    return calls


def _dnd_registry(tmp_path, monkeypatch, *numbers):
    from app.services import preference_registry as pref

    f = tmp_path / "dnd.txt"
    f.write_text("\n".join(numbers) + "\n", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None


def _whatsapp_consented_lead(db, **kw):
    kw.setdefault("phone", "9876543210")
    kw.setdefault("segment", "cafe")
    kw.setdefault("estimated_value", 50000)
    kw.setdefault("proposal_suggested_margin", 70)
    kw.setdefault("call_attempts", 0)
    kw.setdefault("consent_status", "EXPLICIT")
    kw.setdefault("consent_source", "AI_CALL_WHATSAPP_REQUEST")
    return lead(db, **kw)


def test_whatsapp_consent_does_not_license_a_second_ai_call(db, tmp_path, monkeypatch):
    """The exact state a real WHATSAPP_OPT_IN leaves behind: already called
    once, consent recorded. No last_call_date, so the cooldown can't be what
    refuses it; the one-call rule must be."""
    from app.services.calling_agent import CallingAgentService

    _dnd_registry(tmp_path, monkeypatch)
    calls = _dry_voice(monkeypatch)
    l = _whatsapp_consented_lead(db, outreach_stage="AI_INTEREST_DETECTED", ai_call_count=1)

    ok, reason = CallingAgentService.trigger_vapi_call(db, l)

    assert ok is False
    assert reason.startswith("cold_call_refused"), reason
    assert "once per lead" in reason
    assert calls == []


def test_whatsapp_consent_does_not_skip_the_dnd_registry(db, tmp_path, monkeypatch):
    from app.services.calling_agent import CallingAgentService

    _dnd_registry(tmp_path, monkeypatch, "9876543210")
    calls = _dry_voice(monkeypatch)
    l = _whatsapp_consented_lead(db)

    ok, reason = CallingAgentService.trigger_vapi_call(db, l)

    assert ok is False
    assert "preference registry" in reason, reason
    assert calls == []


def test_a_consented_lead_is_still_called_once_as_a_qualification_call(db, tmp_path, monkeypatch):
    """Consent doesn't block a call either: a clean, never-called lead gets
    the ordinary disclosed qualification call, with its constraints."""
    from app.services.calling_agent import CallingAgentService

    _dnd_registry(tmp_path, monkeypatch)
    calls = _dry_voice(monkeypatch)
    l = _whatsapp_consented_lead(db)

    ok, reason = CallingAgentService.trigger_vapi_call(db, l)

    assert ok is True, reason
    assert reason == "qualification_call_placed: dry-run-provider-call"
    assert len(calls) == 1
    assert "opening" in calls[0][1] and "constraints" in calls[0][1]
    assert l.ai_call_count == 1
    assert l.outreach_stage == "AI_CALL_ATTEMPTED"
