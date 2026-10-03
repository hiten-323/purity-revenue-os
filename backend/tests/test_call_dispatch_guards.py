"""Dispatch guards: never-rang attempts are released (not counted, labelled
DISPATCH_FAILED), fleet volume caps, and the founder system alert.

Nothing here dials, sends mail, or talks to a provider.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.database import Base, get_db
from app.models.models import B2BLead, CallHistory
from app.services import founder_alert
from app.services import founder_call_pipeline as pipeline
from app.services.call_intelligence import capture as C, taxonomy as T, volume as VOL
from app.services.call_intelligence.models import CallResult
from app.services.call_intelligence.never_rang import release_never_rang
from conftest import memory_engine

SECRET = "test-admin-secret"
H = {"X-Api-Admin-Secret": SECRET}


@pytest.fixture
def db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _lead(db, **kw):
    kw.setdefault("phone", "9876543210")
    kw.setdefault("company", f"Guard Cafe {db.query(B2BLead).count()}")
    kw.setdefault("segment", "cafe")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


def _dialled(db, lead, *, minutes_ago=2, call_ref=None):
    """What the live dial path leaves: AI_CALL_ATTEMPTED, counted, CALLING."""
    if pipeline.stage_of(lead) in (pipeline.ELIGIBLE, pipeline.AI_NO_ANSWER):
        pipeline.advance(lead, db, pipeline.AI_CALL_ATTEMPTED, note="test dial")
    lead.call_status = "CALLING"
    lead.ai_call_count = (lead.ai_call_count or 0) + 1
    lead.last_call_date = datetime.utcnow() - timedelta(minutes=minutes_ago)
    db.add(CallHistory(lead_id=lead.id, call_date=lead.last_call_date,
                       status="AI_CALL_ATTEMPTED", call_status="CALLING", summary=""))
    if call_ref:
        C.open_call_attempt(db, lead, call_ref=call_ref, provider="nuraveda",
                            started_at=lead.last_call_date)
    db.commit()
    return lead


def _client(monkeypatch, db):
    from app.api import founder_router
    monkeypatch.setenv("API_ADMIN_SECRET", SECRET)
    monkeypatch.delenv("GATEWAY_ADMIN_SECRET", raising=False)
    app = FastAPI()
    app.include_router(founder_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# ── taxonomy ────────────────────────────────────────────────────────────────

def test_never_rang_vocabulary():
    assert "DISPATCH_FAILED" in T.RESULT_OUTCOMES
    assert "DISPATCH_FAILED" in T.NOT_CONNECTED_OUTCOMES
    assert "DISPATCH_FAILED" not in T.CONNECTED_OUTCOMES
    for r in ("DISPATCH_FAILED", "AGENT_NOT_JOINED", "PRE_RING_FAILURE", "CREDITS_EXHAUSTED"):
        assert r in T.NEVER_RANG_TERMINATIONS and r in T.ENGINE_TERMINATIONS
    # a never-rang dial is never relabelled a no-answer
    assert T.terminal_call_status_for("DISPATCH_FAILED") == "DISPATCH_FAILED"
    assert T.SIDECAR_DISPOSITIONS["dispatch_failed"] == "DISPATCH_FAILED"
    assert T.SIDECAR_DISPOSITIONS["credits_exhausted"] == "CREDITS_EXHAUSTED"


# ── fix 7: never-rang attempts are released ─────────────────────────────────

@pytest.mark.parametrize("reason", ["DISPATCH_FAILED", "AGENT_NOT_JOINED",
                                    "PRE_RING_FAILURE", "CREDITS_EXHAUSTED"])
def test_never_rang_status_releases_the_attempt(monkeypatch, db, reason):
    lead = _dialled(db, _lead(db), call_ref="nr-1")
    assert lead.ai_call_count == 1
    r = _client(monkeypatch, db).post("/api/v1/founder/ai-call-status", headers=H, json={
        "lead_id": lead.id, "reason": reason, "call_ref": "nr-1",
        "detail": "agent did not join room within 45000ms"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "released" and body["attempt_released"] is True
    db.expire_all()
    assert lead.ai_call_count == 0
    assert lead.call_status == "DISPATCH_FAILED"
    assert pipeline.stage_of(lead) == pipeline.AI_NO_ANSWER
    assert lead.ai_retry_after is not None and lead.ai_retry_after > datetime.utcnow() + timedelta(hours=19)
    assert not lead.do_not_call
    row = db.query(CallResult).filter(CallResult.call_id == "nr-1").one()
    assert row.status == "FINAL" and row.outcome == "DISPATCH_FAILED"
    assert row.connected is False and row.confidence == "LOW_CONFIDENCE"
    hist = db.query(CallHistory).filter(CallHistory.lead_id == lead.id).all()
    assert [h.call_status for h in hist] == ["DISPATCH_FAILED"]
    assert [h.status for h in hist] == ["DISPATCH_FAILED"]


def test_never_rang_retry_attempt_gives_back_only_that_attempt(db):
    lead = _lead(db)
    _dialled(db, lead)
    pipeline.record_ai_outcome(lead, db, "NO_ANSWER", summary="rang out")
    lead.call_status = "NO_ANSWER"
    db.commit()
    n = lead.ai_call_count
    _dialled(db, lead)
    assert lead.ai_call_count == n + 1
    res = release_never_rang(db, lead, reason="AGENT_NOT_JOINED")
    db.commit()
    assert res["released"] is True
    assert lead.ai_call_count == n
    assert pipeline.stage_of(lead) == pipeline.AI_NO_ANSWER


def test_late_never_rang_report_for_concluded_call_is_a_no_op(monkeypatch, db):
    lead = _dialled(db, _lead(db), call_ref="nr-2")
    client = _client(monkeypatch, db)
    client.post("/api/v1/founder/ai-call-outcome", headers=H,
                json={"lead_id": lead.id, "outcome": "INTERESTED", "call_ref": "nr-2"})
    db.expire_all()
    n = lead.ai_call_count
    r = client.post("/api/v1/founder/ai-call-status", headers=H,
                    json={"lead_id": lead.id, "reason": "DISPATCH_FAILED", "call_ref": "nr-2"})
    assert r.json()["status"] == "already_recorded"
    db.expire_all()
    assert lead.ai_call_count == n and pipeline.stage_of(lead) == pipeline.AI_INTEREST_DETECTED


def test_not_outstanding_lead_is_untouched(db):
    lead = _lead(db, ai_call_count=2, call_status="NO_ANSWER")
    res = release_never_rang(db, lead, reason="DISPATCH_FAILED")
    assert res["released"] is False and lead.ai_call_count == 2
    assert lead.call_status == "NO_ANSWER"


def test_rang_failures_still_count(monkeypatch, db):
    """Busy / no-answer rang the phone: still an attempt (unchanged)."""
    lead = _dialled(db, _lead(db), call_ref="b-1")
    r = _client(monkeypatch, db).post("/api/v1/founder/ai-call-status", headers=H,
                                      json={"lead_id": lead.id, "reason": "BUSY", "call_ref": "b-1"})
    assert r.status_code == 200 and r.json()["fsm_applied"] is True
    db.expire_all()
    assert lead.ai_call_count >= 1 and lead.call_status == "BUSY"


# ── fix 4: volume caps ──────────────────────────────────────────────────────

def test_cycle_cap_defaults(monkeypatch):
    monkeypatch.delenv("AI_CALL_CYCLE_MAX_DIALS", raising=False)
    monkeypatch.delenv("AI_CALL_MAX_INFLIGHT", raising=False)
    assert VOL.cycle_max_dials() == 5
    assert VOL.max_inflight() == 2
    monkeypatch.setenv("AI_CALL_CYCLE_MAX_DIALS", "3")
    monkeypatch.setenv("AI_CALL_MAX_INFLIGHT", "junk")
    assert VOL.cycle_max_dials() == 3 and VOL.max_inflight() == 2


def test_dial_budget_respects_inflight(monkeypatch, db):
    monkeypatch.setenv("AI_CALL_CYCLE_MAX_DIALS", "5")
    monkeypatch.setenv("AI_CALL_MAX_INFLIGHT", "2")
    assert VOL.dial_budget(db) == 2
    _dialled(db, _lead(db, phone="9876500001"))
    assert VOL.inflight_count(db) == 1 and VOL.dial_budget(db) == 1
    _dialled(db, _lead(db, phone="9876500002"))
    assert VOL.dial_budget(db) == 0
    # a CALLING row older than the in-flight window is the reconciler's, not a live call
    _dialled(db, _lead(db, phone="9876500003"), minutes_ago=120)
    assert VOL.inflight_count(db) == 2
    assert VOL.dial_budget(db, limit=0) == 0


def test_dial_budget_explicit_limit_never_raises_cap(monkeypatch, db):
    monkeypatch.setenv("AI_CALL_CYCLE_MAX_DIALS", "1")
    monkeypatch.setenv("AI_CALL_MAX_INFLIGHT", "10")
    assert VOL.dial_budget(db, limit=50) == 1


# ── fix 6: founder system alert ─────────────────────────────────────────────

def test_system_alert_is_rate_limited(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDER_ALERT_STATE_FILE", str(tmp_path / "s.json"))
    sent = []

    def fake(subject, body):
        sent.append((subject, body))
        return {"sent": True}
    now = datetime(2026, 10, 3, 12, 0)
    a = founder_alert.send_system_alert("CREDITS_EXHAUSTED", "Plivo 402", key="credits:plivo",
                                        now=now, sender=fake)
    b = founder_alert.send_system_alert("CREDITS_EXHAUSTED", "Plivo 402", key="credits:plivo",
                                        now=now + timedelta(minutes=5), sender=fake)
    c = founder_alert.send_system_alert("CREDITS_EXHAUSTED", "Plivo 402", key="credits:plivo",
                                        now=now + timedelta(minutes=61), sender=fake)
    assert a["sent"] and not b["sent"] and b["reason"] == "rate_limited" and c["sent"]
    assert len(sent) == 2 and "Credits Exhausted" in sent[0][0] and "Plivo 402" in sent[0][1]
    assert founder_alert.FOUNDER_EMAIL == "hitenjain.12@gmail.com"


def test_system_alert_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDER_ALERT_STATE_FILE", str(tmp_path / "s.json"))

    def boom(subject, body):
        raise OSError("smtp down")
    r = founder_alert.send_system_alert("AGENT_WEDGED", "x", sender=boom)
    assert r["sent"] is False and "smtp" in r["reason"]


def test_system_alert_without_password_does_not_send(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDER_ALERT_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("ZOHO_APP_PASSWORD", "")
    r = founder_alert.send_system_alert("AGENT_WEDGED", "x")
    assert r["sent"] is False and "not configured" in r["reason"]


def test_system_alert_endpoint_requires_admin(monkeypatch, db, tmp_path):
    monkeypatch.setenv("FOUNDER_ALERT_STATE_FILE", str(tmp_path / "s.json"))
    calls = []
    monkeypatch.setattr(founder_alert, "_smtp_send",
                        lambda s, b: calls.append(s) or {"sent": True})
    client = _client(monkeypatch, db)
    assert client.post("/api/v1/founder/system-alert",
                       json={"kind": "AGENT_WEDGED", "detail": "x"}).status_code in (401, 403, 503)
    r = client.post("/api/v1/founder/system-alert", headers=H,
                    json={"kind": "AGENT_WEDGED", "detail": "runner initialization timed out"})
    assert r.status_code == 200 and r.json()["sent"] is True and len(calls) == 1
