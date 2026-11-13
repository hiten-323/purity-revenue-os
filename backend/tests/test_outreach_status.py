"""Unit tests for the live Auto-Outreach Status API & service."""
from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.database import Base, get_db
from app.models.models import B2BLead, CallHistory, EmailDraft, WorkflowEvent, WorkflowExecution
from app.services.smart_outreach import OutreachTouch
from app.services.outreach_intelligence.status import get_live_outreach_status
from app.api.outreach_intelligence_router import router as oi_router
from app.api.endpoints import router as api_router
from conftest import memory_engine


def _setup_db():
    engine = memory_engine()
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__, CallHistory.__table__, WorkflowEvent.__table__,
        EmailDraft.__table__, OutreachTouch.__table__, WorkflowExecution.__table__,
    ])
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine)()


def _setup_client(session):
    app = FastAPI()
    app.include_router(oi_router, prefix="/api/v1")
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


def test_empty_window_reports_no_activity():
    session = _setup_db()
    res = get_live_outreach_status(session, hours=3)
    
    assert res["status"] == "success"
    assert res["overview"]["has_activity"] is False
    assert "NO ACTIVITY in the last 3 hours" in res["overview"]["activity_summary"]
    assert res["overview"]["total_outreach_attempts"] == 0
    assert res["calls"]["attempted"] == 0
    assert res["messages"]["total_attempted"] == 0
    assert res["system_status"]["is_read_only"] is True


def test_calls_in_window_aggregated():
    session = _setup_db()
    now = datetime.utcnow()

    lead = B2BLead(company="Test Cafe", phone="9876543210")
    session.add(lead)
    session.commit()

    # Add call records within 1h ago
    c1 = CallHistory(lead_id=lead.id, call_date=now - timedelta(minutes=30), status="AI_INTERESTED", call_status="COMPLETED")
    c2 = CallHistory(lead_id=lead.id, call_date=now - timedelta(minutes=20), status="AI_NO_ANSWER", call_status="COMPLETED")
    c3 = CallHistory(lead_id=lead.id, call_date=now - timedelta(minutes=10), status="AI_FAILED", call_status="COMPLETED")
    c4 = CallHistory(lead_id=lead.id, call_date=now - timedelta(minutes=5), status="AI_CALLBACK_REQUESTED", call_status="COMPLETED")
    # Add call outside window (5h ago)
    c5 = CallHistory(lead_id=lead.id, call_date=now - timedelta(hours=5), status="AI_INTERESTED", call_status="COMPLETED")

    session.add_all([c1, c2, c3, c4, c5])
    session.commit()

    res = get_live_outreach_status(session, hours=3)
    assert res["overview"]["has_activity"] is True
    assert res["calls"]["attempted"] == 4
    assert res["calls"]["interested"] == 1
    assert res["calls"]["no_answer"] == 1
    assert res["calls"]["failed"] == 1
    assert res["calls"]["callbacks_requested"] == 1

    # Expanding window to 6h includes all 5 calls
    res6 = get_live_outreach_status(session, hours=6)
    assert res6["calls"]["attempted"] == 5


def test_touches_and_failures_aggregated():
    session = _setup_db()
    now = datetime.utcnow()

    lead = B2BLead(company="Hotel Amritsar", email="info@hotelamritsar.com")
    session.add(lead)
    session.commit()

    t1 = OutreachTouch(
        lead_id=lead.id, channel="email", touch_type="WARM_FIRST_TOUCH",
        status="SENT", occurred_at=now - timedelta(minutes=45),
        payload={"message_id": "msg-123"}
    )
    t2 = OutreachTouch(
        lead_id=lead.id, channel="email", touch_type="WARM_FIRST_TOUCH",
        status="FAILED", occurred_at=now - timedelta(minutes=30),
        payload={"error": "BLOCKED: info@hotelamritsar.com — VALIDATED — may draft, not send"}
    )
    session.add_all([t1, t2])
    session.commit()

    res = get_live_outreach_status(session, hours=3)
    assert res["messages"]["total_attempted"] == 2
    assert res["messages"]["by_channel"]["email"]["sent"] == 1
    assert res["messages"]["by_channel"]["email"]["failed"] == 1
    assert any("VALIDATED — may draft, not send" in r for r in res["failures_and_blockers"]["reasons"].keys())


def test_replies_and_consents_aggregated():
    session = _setup_db()
    now = datetime.utcnow()

    lead = B2BLead(company="Roast Cafe", email="info@roast.com", phone="9876543211")
    session.add(lead)
    session.commit()

    ev_reply = WorkflowEvent(
        lead_id=lead.id, event_type="EMAIL_REPLY_RECEIVED", actor="PROSPECT",
        channel="email", payload={"text": "send prices", "is_cold": True},
        occurred_at=now - timedelta(minutes=15)
    )
    ev_consent = WorkflowEvent(
        lead_id=lead.id, event_type="WHATSAPP_CONSENT_RECORDED", actor="SYSTEM",
        channel="whatsapp", payload={"source": "AI_CALL_WHATSAPP_REQUEST", "evidence": "yes whatsapp ok"},
        occurred_at=now - timedelta(minutes=10)
    )
    session.add_all([ev_reply, ev_consent])
    session.commit()

    res = get_live_outreach_status(session, hours=3)
    assert res["replies"]["total"] == 1
    assert res["replies"]["cold_lead_replies"] == 1
    assert res["whatsapp_consents"]["total"] == 1


def test_status_http_endpoints_authentication(monkeypatch):
    monkeypatch.setenv("OUTREACH_STATUS_API_TOKEN", "valid-status-secret-12345")
    session = _setup_db()
    client = _setup_client(session)

    # 1. Missing auth returns 401
    r_unauth = client.get("/api/v1/outreach/status?hours=3")
    assert r_unauth.status_code == 401
    assert "Unauthorized" in r_unauth.json()["detail"]

    # 2. Invalid token returns 403
    r_bad = client.get("/api/v1/outreach/status?hours=3", headers={"Authorization": "Bearer bad-token"})
    assert r_bad.status_code == 403
    assert "Forbidden" in r_bad.json()["detail"]

    # 3. Valid Bearer token on /api/v1/outreach/status returns 200
    r_ok = client.get(
        "/api/v1/outreach/status?hours=3",
        headers={"Authorization": "Bearer valid-status-secret-12345"},
    )
    assert r_ok.status_code == 200
    data = r_ok.json()
    assert data["status"] == "success"
    assert data["window"]["hours"] == 3
    assert data["window"]["timezone"] == "Asia/Kolkata"

    # 4. Valid X-Status-Token header on alias /api/v1/outreach-status returns 200
    r_alias = client.get(
        "/api/v1/outreach-status?hours=1",
        headers={"X-Status-Token": "valid-status-secret-12345"},
    )
    assert r_alias.status_code == 200
    assert r_alias.json()["window"]["hours"] == 1

    # 5. Valid token on /api/v1/outreach-intelligence/status returns 200
    r_oi = client.get(
        "/api/v1/outreach-intelligence/status?hours=3",
        headers={"Authorization": "Bearer valid-status-secret-12345"},
    )
    assert r_oi.status_code == 200
    assert r_oi.json()["status"] == "success"


def test_status_reports_effective_worker_calling_state(monkeypatch):
    session = _setup_db()
    monkeypatch.setenv("AI_CALLING_ENABLED", "0")
    monkeypatch.setenv("AUTO_AI_CALLING_ENABLED", "0")
    now = datetime.utcnow()
    session.add(WorkflowEvent(
        lead_id=None, event_type="HEARTBEAT", actor="worker", channel="ops",
        payload={"component": "worker", "detail": {
            "ai_calling_armed": True,
            "ai_call_cycle": {"considered": 10, "dialed": 3, "refused": 7},
        }},
        occurred_at=now - timedelta(seconds=5),
    ))
    session.commit()
    res = get_live_outreach_status(session, hours=1)
    system = res["system_status"]
    assert system["api_process_flags"]["ai_calling_enabled"] is False
    assert system["ai_calling_scheduler"]["enabled"] is True
    assert system["ai_calling_scheduler"]["last_cycle"]["dialed"] == 3
    assert system["ai_calling_enabled"] is True


def test_status_health_endpoint():
    session = _setup_db()
    client = _setup_client(session)

    # Health check is public read-only and returns 200
    r_health = client.get("/api/v1/outreach/status/health")
    assert r_health.status_code == 200
    data = r_health.json()
    assert data["status"] == "healthy"
    assert data["api_reachable"] is True
    assert data["database"] == "connected"
    assert data["is_read_only"] is True
    assert "timezone" in data
    assert "auth_configured" in data


def test_status_is_strictly_read_only(monkeypatch):
    monkeypatch.setenv("OUTREACH_STATUS_API_TOKEN", "valid-status-secret-12345")
    session = _setup_db()
    client = _setup_client(session)

    # Pre-populate lead
    lead = B2BLead(company="Read-Only Cafe", status="NEW")
    session.add(lead)
    session.commit()
    initial_lead_count = session.query(B2BLead).count()
    initial_touch_count = session.query(OutreachTouch).count()
    initial_call_count = session.query(CallHistory).count()
    initial_event_count = session.query(WorkflowEvent).count()

    # Query status endpoint
    r = client.get(
        "/api/v1/outreach/status?hours=3",
        headers={"Authorization": "Bearer valid-status-secret-12345"},
    )
    assert r.status_code == 200

    # Ensure zero mutation in any DB table
    assert session.query(B2BLead).count() == initial_lead_count
    assert session.query(OutreachTouch).count() == initial_touch_count
    assert session.query(CallHistory).count() == initial_call_count
    assert session.query(WorkflowEvent).count() == initial_event_count
    assert session.query(B2BLead).first().status == "NEW"
