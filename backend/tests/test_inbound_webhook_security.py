"""
Production Security & Consent Isolation Tests for Inbound Lead Webhook.
Acceptance Test for Phase 1 & Phase 2 of Founder Execution Order.
"""
import hashlib
import hmac
import json
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from sqlalchemy.orm import sessionmaker
from app.database.database import get_db
from app.models.models import Base, B2BLead, WorkflowEvent
from app.main import app
from conftest import memory_engine

client = TestClient(app)
TEST_SECRET = "06d4b8573a04785c42e906f6145970754206d286b05d05d3ef0480f7a9072b7c"


@pytest.fixture(autouse=True)
def setup_webhook_env(monkeypatch):
    monkeypatch.setenv("INBOUND_WEBHOOK_SECRET", TEST_SECRET)
    engine = memory_engine()
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setitem(globals(), "SessionLocal", TestSession)
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _sign(body_bytes: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def test_inbound_webhook_rejects_missing_signature():
    payload = {
        "name": "Test Unsigned",
        "phone": "9811223344",
        "email": "unsigned@example.com",
        "company": f"Unsigned Cafe {uuid.uuid4().hex[:6]}",
        "source": "website_form",
    }
    resp = client.post("/api/v1/inbound/lead-webhook", json=payload)
    assert resp.status_code == 401
    assert "Missing X-Inbound-Signature" in resp.json()["detail"]


def test_inbound_webhook_rejects_invalid_signature():
    payload = {
        "name": "Test Bad Sig",
        "phone": "9811223345",
        "email": "badsig@example.com",
        "company": f"Bad Sig Cafe {uuid.uuid4().hex[:6]}",
        "source": "website_form",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Inbound-Signature": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
    }
    resp = client.post("/api/v1/inbound/lead-webhook", content=raw, headers=headers)
    assert resp.status_code == 403
    assert "Invalid webhook signature" in resp.json()["detail"]


def test_inbound_webhook_rejects_expired_timestamp():
    payload = {
        "name": "Test Expired",
        "phone": "9811223346",
        "email": "expired@example.com",
        "company": f"Expired Cafe {uuid.uuid4().hex[:6]}",
        "source": "website_form",
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = _sign(raw)
    expired_ts = str(int(time.time() - 400))  # 400s old > 300s window
    headers = {
        "Content-Type": "application/json",
        "X-Inbound-Signature": f"sha256={sig}",
        "X-Inbound-Timestamp": expired_ts,
    }
    resp = client.post("/api/v1/inbound/lead-webhook", content=raw, headers=headers)
    assert resp.status_code == 400
    assert "timestamp expired" in resp.json()["detail"]


def test_inbound_webhook_rejects_replay():
    u_id = uuid.uuid4().hex[:6]
    payload = {
        "name": "Test Replay",
        "phone": f"9877{int(u_id, 16) % 1000000:06d}",
        "email": f"replay_{u_id}@example.com",
        "company": f"Replay Cafe {u_id}",
        "source": "website_form",
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = _sign(raw)
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Inbound-Signature": f"sha256={sig}",
        "X-Inbound-Timestamp": ts,
        "X-Inbound-Event-Id": f"evt_{u_id}",
    }
    # First request: succeeds
    resp1 = client.post("/api/v1/inbound/lead-webhook", content=raw, headers=headers)
    assert resp1.status_code == 200

    # Second request (replayed identical event): rejected with 409
    resp2 = client.post("/api/v1/inbound/lead-webhook", content=raw, headers=headers)
    assert resp2.status_code == 409
    assert "Replay detected" in resp2.json()["detail"]


def test_inbound_webhook_enforces_isolated_email_consent():
    u_id = uuid.uuid4().hex[:6]
    payload = {
        "name": "Priya Verma",
        "phone": f"9866{int(u_id, 16) % 1000000:06d}",
        "email": f"priya_{u_id}@roastercafe.com",
        "company": f"Priya Roasters {u_id}",
        "city": "Bengaluru",
        "source": "website_form",
        "inquiry_text": "Need wholesale pricing list via email for our cafe chain.",
        "business_type": "cafe",
        "consent_explicit": True,
        "consent_text": "I agree to receive wholesale coffee price lists and proposals via email.",
        "consent_channels": ["email"],
        "consent_evidence_url": "https://purepantryprovisions.com/wholesale#form",
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = _sign(raw)
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Inbound-Signature": f"sha256={sig}",
        "X-Inbound-Timestamp": ts,
        "X-Inbound-Event-Id": f"evt_email_{u_id}",
    }
    resp = client.post("/api/v1/inbound/lead-webhook", content=raw, headers=headers)
    assert resp.status_code == 200
    lead_id = resp.json()["lead_id"]

    db = SessionLocal()
    try:
        events = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead_id).all()
        types = {e.event_type for e in events}
        # Email consent recorded
        assert "EMAIL_AUTHORIZATION_RECORDED" in types
        # MUST NOT leak to WhatsApp or Voice
        assert "WHATSAPP_CONSENT_RECORDED" not in types
        assert "CALL_AUTHORIZATION_RECORDED" not in types
    finally:
        db.close()


def test_inbound_webhook_enforces_isolated_whatsapp_consent():
    u_id = uuid.uuid4().hex[:6]
    payload = {
        "name": "Karan Singhania",
        "phone": f"9855{int(u_id, 16) % 1000000:06d}",
        "email": f"karan_{u_id}@singhaniabrews.com",
        "company": f"Singhania Brews {u_id}",
        "city": "Mumbai",
        "source": "website_form",
        "inquiry_text": "Send product catalogue directly to my WhatsApp please.",
        "business_type": "distributor",
        "consent_explicit": True,
        "consent_text": "I agree to receive the Purity Beans catalogue and wholesale offers on WhatsApp.",
        "consent_channels": ["whatsapp"],
        "consent_evidence_url": "https://purepantryprovisions.com/distributor#form",
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = _sign(raw)
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Inbound-Signature": f"sha256={sig}",
        "X-Inbound-Timestamp": ts,
        "X-Inbound-Event-Id": f"evt_wa_{u_id}",
    }
    resp = client.post("/api/v1/inbound/lead-webhook", content=raw, headers=headers)
    assert resp.status_code == 200
    lead_id = resp.json()["lead_id"]

    db = SessionLocal()
    try:
        events = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead_id).all()
        types = {e.event_type for e in events}
        # WhatsApp consent recorded
        assert "WHATSAPP_CONSENT_RECORDED" in types
        # MUST NOT leak to Email or Voice
        assert "EMAIL_AUTHORIZATION_RECORDED" not in types
        assert "CALL_AUTHORIZATION_RECORDED" not in types
    finally:
        db.close()