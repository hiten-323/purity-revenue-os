"""
#35 — the whole WhatsApp lifecycle, end to end, through the real HTTP
endpoints, with nothing able to leave the process.

    no consent -> refused
    AI call: "yes, WhatsApp on this number" -> consent bound to that number
    send -> one provider call, recorded, message id kept
    double-click -> refused, provider still called once
    delivery receipt -> the outbound row says DELIVERED
    business replies "STOP" -> revoked
    send again -> refused, provider still called once
    a different business asks for a person -> founder queue -> call completed

Tripwires: every outbound socket this codebase uses (httpx, urllib, smtplib)
raises if touched, and the one WhatsApp transport is replaced by a recorder.
The routers are the production ones; only get_db is swapped for an in-memory
database. Not exercised here: admin auth on /whatsapp/send, which app.main's
middleware applies (importing app.main would start its workers).
test_api_hardening covers require_api_admin itself, on a synthetic route.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, OutboundWhatsApp, WorkflowEvent
from conftest import memory_engine

ADMIN = {"X-Api-Admin-Secret": "sandbox-admin"}
HOOK = {"x-webhook-secret": "sandbox-hook"}


class _Tripwire(Exception):
    pass


def _refuse(*_a, **_k):
    raise _Tripwire("a real network call was attempted in the sandbox")


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    import smtplib
    import urllib.request

    import httpx

    # The real-network transports, not httpx.Client: TestClient is an
    # httpx.Client too, routed through its own in-process transport.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)
    monkeypatch.setattr(urllib.request, "urlopen", _refuse)
    monkeypatch.setattr(smtplib, "SMTP", _refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _refuse)

    monkeypatch.setenv("API_ADMIN_SECRET", "sandbox-admin")
    monkeypatch.setenv("WHATSAPP_WEBHOOK_SECRET", "sandbox-hook")
    for k, v in {"AISENSY_ENABLED": "1", "AISENSY_API_KEY": "sandbox",
                 "WHATSAPP_TEMPLATE": "Purity Outreach", "WHATSAPP_CAMPAIGN_LIVE": "1"}.items():
        monkeypatch.setenv(k, v)
    dnd = tmp_path / "dnd.txt"
    dnd.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(dnd))

    from app.services import whatsapp_aisensy as transport
    sent = []

    def _record(phone, template, **_k):
        sent.append((phone, template))
        return transport.SendResult(status="sent", message_id=f"wamid.sandbox.{len(sent)}",
                                    provider_accepted=True, http_status=200,
                                    reason="sandbox recorder")
    monkeypatch.setattr(transport, "send_template", _record)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    from app.api import endpoints, founder_router
    from app.database.database import get_db

    app = FastAPI()
    app.include_router(endpoints.router, prefix="/api/v1")
    app.include_router(founder_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), db, sent
    db.close()
    engine.dispose()


def test_the_whatsapp_lifecycle_end_to_end(sandbox):
    client, db, sent = sandbox
    lead = B2BLead(company="Sandbox Cafe", phone="+91 98765 43210", segment="cafe")
    db.add(lead)
    db.commit()

    # 1. No consent: refused before the provider.
    r = client.post("/api/v1/whatsapp/send", json={"lead_id": lead.id})
    assert r.status_code == 409 and "opt-in" in r.text
    assert sent == []

    # 2. On the AI call they chose WhatsApp, on the number we called.
    r = client.post("/api/v1/founder/ai-call-outcome", headers=ADMIN,
                    json={"lead_id": lead.id, "outcome": "WHATSAPP_OPT_IN",
                          "summary": "yes, send the catalogue on WhatsApp, this number is fine",
                          "preferred_channel": "WHATSAPP"})
    assert r.status_code == 200, r.text
    db.refresh(lead)
    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_phone == "+91 98765 43210"

    # 3. Send: exactly one provider call, recorded with its message id.
    r = client.post("/api/v1/whatsapp/send", json={"lead_id": lead.id})
    assert r.status_code == 200 and r.json()["status"] == "sent", r.text
    assert len(sent) == 1
    row = db.query(OutboundWhatsApp).filter(OutboundWhatsApp.lead_id == lead.id,
                                            OutboundWhatsApp.status == "SENT").one()
    assert row.whatsapp_message_id == "wamid.sandbox.1"

    # 4. A double-click does not reach the provider.
    r = client.post("/api/v1/whatsapp/send", json={"lead_id": lead.id})
    assert r.json()["status"] == "blocked" and "duplicate" in r.json()["reason"]
    assert len(sent) == 1

    # 5. Delivery is learned from the webhook, never assumed from the send.
    r = client.post("/api/v1/whatsapp/webhook", headers=HOOK,
                    json={"messageId": "wamid.sandbox.1", "status": "delivered"})
    assert r.status_code == 200, r.text
    db.refresh(row)
    assert row.status == "DELIVERED"

    # 6. They reply STOP: revoked, with their words as the evidence.
    r = client.post("/api/v1/whatsapp/webhook", headers=HOOK,
                    json={"type": "message", "from": "919876543210", "text": "STOP"})
    assert r.status_code == 200, r.text
    db.refresh(lead)
    assert lead.consent_status == "REVOKED"
    assert db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type == "WHATSAPP_CONSENT_REVOKED").count() == 1

    # 7. Nothing reaches them after that.
    r = client.post("/api/v1/whatsapp/send", json={"lead_id": lead.id})
    assert r.status_code == 409 and "revoked" in r.text.lower()
    assert len(sent) == 1, "one message in the whole lifecycle"


def test_a_request_for_a_person_reaches_the_founder_end_to_end(sandbox):
    client, db, sent = sandbox
    lead = B2BLead(company="Sandbox Distributors", phone="9876512345", segment="distributor")
    db.add(lead)
    db.commit()

    r = client.post("/api/v1/founder/ai-call-outcome", headers=ADMIN,
                    json={"lead_id": lead.id, "outcome": "HUMAN_HANDOFF",
                          "summary": "wants to discuss margins with the team",
                          "callback_window": "tomorrow 11am"})
    assert r.status_code == 200 and r.json()["stage"] == "FOUNDER_CALL_REQUESTED", r.text

    q = client.get("/api/v1/founder/call-queue", headers=ADMIN).json()
    assert [i["lead_id"] for i in q["items"]] == [lead.id]
    assert "tomorrow 11am" in q["items"][0]["note"]

    r = client.post(f"/api/v1/founder/call-queue/{lead.id}/complete", headers=ADMIN,
                    json={"outcome": "PROPOSAL_REQUESTED", "note": "send terms by Friday"})
    assert r.status_code == 200 and r.json()["stage"] == "FOUNDER_CALL_COMPLETED", r.text
    assert client.get("/api/v1/founder/call-queue", headers=ADMIN).json()["count"] == 0
    assert sent == [], "a handoff sends nothing on its own"
