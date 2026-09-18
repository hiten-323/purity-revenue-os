"""
The HTTP boundary agent.js actually calls: POST /founder/ai-call-outcome.

founder_call_pipeline.py pins the state-machine invariants against direct
calls to record_ai_outcome(). This file pins what only exists at the HTTP
layer itself: that the route fails closed with no configured secret, refuses
a wrong one, and that a webhook/tool retry re-posting the SAME outcome is a
no-op rather than a second attempt at a strict one-shot state machine.

A minimal app mounting the real founder_router (not the full app.main.app)
keeps this to the boundary under audit and off the real database -- get_db is
overridden to an in-memory session per test.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.database import Base, get_db
from app.models.models import B2BLead, CallHistory, LeadInteraction, WorkflowEvent, WorkflowExecution
from app.api import founder_router
from conftest import memory_engine


def _client(monkeypatch, secret="test-admin-secret"):
    if secret is None:
        monkeypatch.delenv("API_ADMIN_SECRET", raising=False)
        monkeypatch.delenv("GATEWAY_ADMIN_SECRET", raising=False)
    else:
        monkeypatch.setenv("API_ADMIN_SECRET", secret)
        monkeypatch.delenv("GATEWAY_ADMIN_SECRET", raising=False)

    engine = memory_engine()
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__, CallHistory.__table__,
        LeadInteraction.__table__, WorkflowEvent.__table__, WorkflowExecution.__table__,
    ])
    from sqlalchemy.orm import sessionmaker
    session = sessionmaker(bind=engine)()

    app = FastAPI()
    app.include_router(founder_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: session

    return TestClient(app), session


def _lead(session, **kw):
    lead = B2BLead(company=kw.pop("company", "Test Cafe"),
                   phone=kw.pop("phone", "9876543210"),
                   segment=kw.pop("segment", "cafe"), **kw)
    session.add(lead)
    session.commit()
    return lead


# --------------------------------------------------------- fail-closed auth --

def test_endpoint_fails_closed_with_no_secret_configured(monkeypatch):
    client, session = _client(monkeypatch, secret=None)
    lead = _lead(session)

    resp = client.post("/api/v1/founder/ai-call-outcome",
                       json={"lead_id": lead.id, "outcome": "NOT_INTERESTED"})
    assert resp.status_code == 503
    # And nothing was recorded -- a disabled gate must not partially apply.
    assert session.get(B2BLead, lead.id).call_outcome_last is None


def test_endpoint_refuses_a_missing_secret(monkeypatch):
    """require_api_admin answers a missing secret the same way as a wrong one
    (503, not 401) so a caller cannot use the response to learn whether the
    admin secret is configured at all -- see auth.py."""
    client, session = _client(monkeypatch)
    lead = _lead(session)

    resp = client.post("/api/v1/founder/ai-call-outcome",
                       json={"lead_id": lead.id, "outcome": "NOT_INTERESTED"})
    assert resp.status_code == 503
    assert session.get(B2BLead, lead.id).call_outcome_last is None


def test_endpoint_refuses_a_wrong_secret(monkeypatch):
    client, session = _client(monkeypatch)
    lead = _lead(session)

    resp = client.post(
        "/api/v1/founder/ai-call-outcome",
        json={"lead_id": lead.id, "outcome": "NOT_INTERESTED"},
        headers={"X-Api-Admin-Secret": "guessed-wrong"},
    )
    assert resp.status_code == 503
    assert session.get(B2BLead, lead.id).call_outcome_last is None


def test_endpoint_accepts_the_configured_secret(monkeypatch):
    client, session = _client(monkeypatch)
    lead = _lead(session)

    resp = client.post(
        "/api/v1/founder/ai-call-outcome",
        json={"lead_id": lead.id, "outcome": "NOT_INTERESTED"},
        headers={"X-Api-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "recorded"


# ---------------------------------------------------------------- identity --

def test_endpoint_404s_an_unknown_lead(monkeypatch):
    client, session = _client(monkeypatch)
    resp = client.post(
        "/api/v1/founder/ai-call-outcome",
        json={"lead_id": 999999, "outcome": "NOT_INTERESTED"},
        headers={"X-Api-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 404


def test_endpoint_rejects_an_unrecognised_outcome_as_a_400_not_a_guess(monkeypatch):
    """agent.js only ever sends a value from its own OUTCOME_VALUES/
    ENGINE_OUTCOME_VALUES lists, but the boundary must not trust that -- an
    unrecognised string from a compromised or mismatched client must be
    refused, never silently mapped to something plausible."""
    client, session = _client(monkeypatch)
    lead = _lead(session)

    resp = client.post(
        "/api/v1/founder/ai-call-outcome",
        json={"lead_id": lead.id, "outcome": "maybe-interested-ish"},
        headers={"X-Api-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 400
    assert session.get(B2BLead, lead.id).call_outcome_last is None


# ------------------------------------------------------- idempotent retries --

def test_a_retried_identical_outcome_is_a_no_op_not_an_error(monkeypatch):
    """The webhook/tool-retry case: a network layer re-sends the same POST
    after a timeout even though the first one landed. record_ai_outcome()'s
    advance() is a strict one-shot FSM and would raise ValueError on a second
    call for the same lead -- this must not surface as a 400."""
    client, session = _client(monkeypatch)
    lead = _lead(session)
    headers = {"X-Api-Admin-Secret": "test-admin-secret"}

    first = client.post("/api/v1/founder/ai-call-outcome",
                        json={"lead_id": lead.id, "outcome": "WHATSAPP_OPT_IN"},
                        headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "recorded"

    retry = client.post("/api/v1/founder/ai-call-outcome",
                        json={"lead_id": lead.id, "outcome": "WHATSAPP_OPT_IN"},
                        headers=headers)
    assert retry.status_code == 200
    assert retry.json()["status"] == "already_recorded"

    # And the retry did not re-run the consent side effects a second time.
    lead = session.get(B2BLead, lead.id)
    assert lead.consent_status == "EXPLICIT"
    assert len(session.query(WorkflowEvent)
               .filter(WorkflowEvent.lead_id == lead.id,
                       WorkflowEvent.event_type == "NEXT_ACTION_SET")
               .all()) == 1


def test_a_different_outcome_after_the_call_is_already_settled_is_refused(monkeypatch):
    """Not a retry: a SECOND, DIFFERENT outcome for a lead already past its
    one allowed call. MAX_AI_COLD_CALLS_PER_LEAD is 1, so there is no
    legitimate way for a real second outcome to exist -- this must surface as
    an error, not be accepted as if the call happened twice."""
    client, session = _client(monkeypatch)
    lead = _lead(session)
    headers = {"X-Api-Admin-Secret": "test-admin-secret"}

    first = client.post("/api/v1/founder/ai-call-outcome",
                        json={"lead_id": lead.id, "outcome": "NOT_INTERESTED"},
                        headers=headers)
    assert first.status_code == 200

    second = client.post("/api/v1/founder/ai-call-outcome",
                         json={"lead_id": lead.id, "outcome": "WHATSAPP_OPT_IN"},
                         headers=headers)
    assert second.status_code == 400

    # The terminal stage from the first, real outcome must not have been
    # overwritten by the rejected second attempt.
    lead = session.get(B2BLead, lead.id)
    assert lead.call_outcome_last == "NOT_INTERESTED"
    assert (lead.consent_status or "UNKNOWN").upper() == "UNKNOWN"


def test_a_number_read_out_on_the_call_reaches_the_pipeline(monkeypatch):
    """agent.js sends whatsapp_number when the business gives a different
    WhatsApp number. If the webhook dropped it, consent would silently bind
    to the dialled number instead of the one they asked for."""
    client, session = _client(monkeypatch)
    lead = _lead(session, phone="+919876543210", segment="cafe")

    resp = client.post(
        "/api/v1/founder/ai-call-outcome",
        json={"lead_id": lead.id, "outcome": "WHATSAPP_OPT_IN",
              "summary": "send it to 98123 45678", "whatsapp_number": "98123 45678"},
        headers={"X-Api-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 200, resp.text
    lead = session.get(B2BLead, lead.id)
    assert lead.consent_phone == "9812345678"


def test_what_the_call_learned_reaches_the_pipeline(monkeypatch):
    """The fields agent.js reports beyond the outcome. Dropped here, the
    learning loop would count nothing and look like no call learned anything."""
    from app.models.models import WorkflowEvent

    client, session = _client(monkeypatch)
    lead = _lead(session, phone="+919876543211", segment="distributor")

    resp = client.post(
        "/api/v1/founder/ai-call-outcome",
        json={"lead_id": lead.id, "outcome": "SEND_INFO_EMAIL", "summary": "email it",
              "preferred_channel": "EMAIL", "handles_instant_coffee": "YES",
              "decision_maker": "UNKNOWN", "objection": "EXISTING_SUPPLIER"},
        headers={"X-Api-Admin-Secret": "test-admin-secret"},
    )
    assert resp.status_code == 200, resp.text
    ev = (session.query(WorkflowEvent)
          .filter(WorkflowEvent.lead_id == lead.id,
                  WorkflowEvent.event_type == "AI_CALL_DETAILS").one())
    assert ev.payload["preferred_channel"] == "EMAIL"
    assert ev.payload["objection"] == "EXISTING_SUPPLIER"


# ── #27: the founder call queue over HTTP ────────────────────────────────────

def test_a_handoff_on_the_call_shows_up_in_the_founder_queue_and_can_be_closed(monkeypatch):
    client, session = _client(monkeypatch)
    lead = _lead(session, phone="9876543212", company="Queue Cafe")
    hdr = {"X-Api-Admin-Secret": "test-admin-secret"}

    r = client.post("/api/v1/founder/ai-call-outcome", headers=hdr,
                    json={"lead_id": lead.id, "outcome": "HUMAN_HANDOFF",
                          "summary": "wants to talk about margins"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "FOUNDER_CALL_REQUESTED", "the response says where the lead really is"

    q = client.get("/api/v1/founder/call-queue", headers=hdr)
    assert q.status_code == 200, q.text
    items = q.json()["items"]
    assert [i["lead_id"] for i in items] == [lead.id]
    assert items[0]["brief"]["business"] == "Queue Cafe"
    assert "margins" in items[0]["note"]

    done = client.post(f"/api/v1/founder/call-queue/{lead.id}/complete", headers=hdr,
                       json={"outcome": "NEEDS_PROPOSAL", "note": "send terms"})
    assert done.status_code == 200, done.text
    assert done.json()["stage"] == "FOUNDER_CALL_COMPLETED"
    assert client.get("/api/v1/founder/call-queue", headers=hdr).json()["count"] == 0

    again = client.post(f"/api/v1/founder/call-queue/{lead.id}/complete", headers=hdr,
                        json={"outcome": "NEEDS_PROPOSAL"})
    assert again.status_code == 409, "a finished call is not finished twice"


def test_the_founder_queue_is_not_public(monkeypatch):
    """Every item carries a phone number and what the business said."""
    client, _ = _client(monkeypatch)
    # require_api_admin refuses with 503, the same as every admin route.
    assert client.get("/api/v1/founder/call-queue").status_code == 503
    assert client.get("/api/v1/founder/call-queue",
                      headers={"X-Api-Admin-Secret": "wrong"}).status_code == 503
    assert client.post("/api/v1/founder/call-queue/1/complete",
                       json={"outcome": "X"}).status_code == 503
