"""29 Oct 2026: concurrent reports for the same call must not 500 or lose the outcome.

Root cause of the 26-27 Oct HTTP 500s on /founder/ai-call-outcome: two reports
for one call_ref (agent outcome + voice-server status, or the tool report +
the close-handler fallback) both found no CallResult and both INSERTed; the
loser's UNIQUE(call_results.call_id) IntegrityError poisoned the session,
record_ai_outcome's swallow then hit PendingRollbackError, the request 500ed
and the outcome was rolled back.

These tests use a real file-backed SQLite DB in WAL mode (same driver setup as
production, pysqlite lazy BEGIN) so SAVEPOINT semantics are the real ones.
Nothing here dials, sends, or talks to a provider.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database.database import Base, get_db
from app.models.models import B2BLead, CallHistory
from app.services import founder_call_pipeline as pipeline
from app.services.call_intelligence import capture as C
from app.services.call_intelligence.models import CallResult

SECRET = "test-admin-secret"
H = {"X-Api-Admin-Secret": SECRET}


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def _dialled_lead(s, phone="9000000001"):
    lead = B2BLead(phone=phone, company=f"Race Cafe {phone[-4:]}", segment="cafe", city="Bathinda")
    s.add(lead)
    s.commit()
    pipeline.advance(lead, s, pipeline.AI_CALL_ATTEMPTED, note="test dial")
    lead.call_status = "CALLING"
    lead.ai_call_count = 1
    lead.last_call_date = datetime.utcnow() - timedelta(minutes=5)
    s.add(CallHistory(lead_id=lead.id, call_date=lead.last_call_date,
                      status="AI_CALL_ATTEMPTED", call_status="CALLING", summary="dispatched"))
    s.commit()
    return lead.id


def _miss_once(monkeypatch):
    """Make the first pass's find_result() lookups miss, exactly as a report that checked for
    the row just before the other report committed it."""
    real = C.find_result
    state = {"n": 0}

    def fake(db, call_id):
        state["n"] += 1
        # both lookups in the first pass (row lookup + fallback-id check) ran
        # before the other report committed
        return None if state["n"] <= 2 else real(db, call_id)

    monkeypatch.setattr(C, "find_result", fake)
    monkeypatch.setattr(C, "latest_open_result", lambda db, lead_id: None)
    return state


def test_lost_insert_race_merges_instead_of_poisoning_the_session(factory, monkeypatch):
    s0 = factory()
    lead_id = _dialled_lead(s0)
    s0.close()

    # Report A wins: the voice server's no-answer status lands first.
    a = factory()
    C.finalize_call(a, a.get(B2BLead, lead_id), termination_reason="NO_ANSWER",
                    source="voice_server", meta={"call_ref": "ref-race"})
    a.commit()
    a.close()

    # Report B (the agent's real outcome) checked before A committed.
    b = factory()
    lead = b.get(B2BLead, lead_id)
    # an earlier change in the same request transaction must survive the savepoint rollback
    b.add(CallHistory(lead_id=lead_id, call_date=datetime.utcnow(), status="MARKER",
                      call_status="MARKER", summary="pre-finalize change"))
    _miss_once(monkeypatch)
    row = C.finalize_call(b, lead, fsm_key="CALLBACK_REQUESTED", source="voice_agent",
                          summary="call back tomorrow",
                          meta={"call_ref": "ref-race",
                                "answered_at": (datetime.utcnow() - timedelta(seconds=60)).isoformat()})
    assert row is not None and row.call_id == "ref-race"
    b.commit()                         # would raise PendingRollbackError before the fix
    b.close()

    c = factory()
    rows = c.query(CallResult).filter(CallResult.call_id == "ref-race").all()
    assert len(rows) == 1 and rows[0].status == "FINAL"
    raced_outcome = rows[0].outcome
    assert c.query(CallHistory).filter(CallHistory.summary == "pre-finalize change").count() == 1
    raced_status = c.get(B2BLead, lead_id).call_status
    assert raced_status != "CALLING"
    c.close()

    # Control: the same two reports WITHOUT the race give the same result,
    # i.e. the lost-race path applies the normal precedence rules.
    monkeypatch.undo()
    s1 = factory()
    ctl = _dialled_lead(s1, "9000000009")
    C.finalize_call(s1, s1.get(B2BLead, ctl), termination_reason="NO_ANSWER",
                    source="voice_server", meta={"call_ref": "ref-ctl"})
    s1.commit()
    C.finalize_call(s1, s1.get(B2BLead, ctl), fsm_key="CALLBACK_REQUESTED", source="voice_agent",
                    summary="call back tomorrow",
                    meta={"call_ref": "ref-ctl",
                          "answered_at": (datetime.utcnow() - timedelta(seconds=60)).isoformat()})
    s1.commit()
    assert s1.query(CallResult).filter(CallResult.call_id == "ref-ctl").one().outcome == raced_outcome
    assert s1.get(B2BLead, ctl).call_status == raced_status
    s1.close()


def _client(monkeypatch, factory):
    from app.api import founder_router
    monkeypatch.setenv("API_ADMIN_SECRET", SECRET)
    monkeypatch.delenv("GATEWAY_ADMIN_SECRET", raising=False)
    app = FastAPI()
    app.include_router(founder_router.router, prefix="/api/v1")

    def _get_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app, raise_server_exceptions=False)


def test_outcome_endpoint_does_not_500_on_lost_race(factory, monkeypatch):
    s0 = factory()
    lead_id = _dialled_lead(s0, "9000000002")
    C.finalize_call(s0, s0.get(B2BLead, lead_id), termination_reason="NO_ANSWER",
                    source="voice_server", meta={"call_ref": "ref-http"})
    s0.commit()
    s0.close()
    client = _client(monkeypatch, factory)
    _miss_once(monkeypatch)
    r = client.post("/api/v1/founder/ai-call-outcome", headers=H, json={
        "lead_id": lead_id, "outcome": "NOT_INTERESTED", "summary": "not interested",
        "call_ref": "ref-http"})
    assert r.status_code == 200, r.text
    s = factory()
    rows = s.query(CallResult).filter(CallResult.call_id == "ref-http").all()
    assert len(rows) == 1 and rows[0].status == "FINAL"
    s.close()


def test_concurrent_outcome_and_status_reports_never_500(factory, monkeypatch):
    client = _client(monkeypatch, factory)
    s0 = factory()
    leads = [_dialled_lead(s0, f"90000001{i:02d}") for i in range(12)]
    s0.close()
    codes = []

    for i, lead_id in enumerate(leads):
        ref = f"ref-conc-{i}"
        gate = threading.Barrier(2)

        def outcome():
            gate.wait()
            codes.append(client.post("/api/v1/founder/ai-call-outcome", headers=H, json={
                "lead_id": lead_id, "outcome": "NOT_INTERESTED", "summary": "no", "call_ref": ref}).status_code)

        def status():
            gate.wait()
            codes.append(client.post("/api/v1/founder/ai-call-status", headers=H, json={
                "lead_id": lead_id, "reason": "NO_ANSWER", "call_ref": ref}).status_code)

        ts = [threading.Thread(target=outcome), threading.Thread(target=status)]
        [t.start() for t in ts]
        [t.join(60) for t in ts]

    assert codes and all(c < 500 for c in codes), codes
    s = factory()
    for i in range(len(leads)):
        assert s.query(CallResult).filter(CallResult.call_id == f"ref-conc-{i}").count() == 1
    s.close()
