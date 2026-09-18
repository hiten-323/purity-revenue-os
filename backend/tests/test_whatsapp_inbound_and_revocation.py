"""
What a WhatsApp message FROM a business does to our permission to message it,
and what a revocation does to every send after it.

Found while verifying the WhatsApp checklist (#30-#32), all in code that had
no test at all:

  - The inbound webhook marked every message EXPLICIT without reading it, so
    a business replying "STOP" was recorded as opting in, and a business that
    had revoked was flipped back to opted-in by its next message.
  - consent_check let any lead with status REPLIED through as "opt-in + 24h
    window open", checking neither the window nor the channel. An EMAIL reply
    -- "not interested" included -- licensed WhatsApp, and a REVOKED lead
    still passed because replying "stop" is itself a reply.
  - The manual send endpoint and the reminder executor had no duplicate
    guard; AiSensy takes no idempotency key.

No test here can reach AiSensy: AISENSY_ENABLED is unset, so the transport
answers not_configured before building a request. Tests that need it
configured hand it a fake client that raises, or replace it outright.

Also here (#33/#34): a request whose answer was lost may have been accepted,
so it is "unknown", never "failed" -- and nothing retries an unknown send.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, WorkflowEvent
from app.services import whatsapp_consent as wc
from app.services.whatsapp_sender import consent_check, send_whatsapp
from conftest import memory_engine


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("AISENSY_ENABLED", raising=False)
    engine = memory_engine()
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def _lead(db, **kw):
    kw.setdefault("company", "Inbound Test Traders")
    kw.setdefault("phone", "+91-98765-43210")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


def _events(db, lead, kind):
    return (db.query(WorkflowEvent)
            .filter(WorkflowEvent.lead_id == lead.id, WorkflowEvent.event_type == kind).all())


# ── #31: an inbound message is read before it decides anything ───────────────

@pytest.mark.parametrize("text", [
    "STOP", "stop.", "Unsubscribe", "please don't message me again",
    "do not contact us", "remove me", "no more messages please", "band karo",
    "Stop sending these",
])
def test_an_inbound_stop_revokes_it_never_grants(db, text):
    lead = _lead(db, consent_status="EXPLICIT", consent_phone="9876543210",
                 whatsapp_number="9876543210")
    out = wc.capture_whatsapp_inbound(lead, db, sender="919876543210", text=text)

    assert out.get("revoked") is True, text
    assert lead.consent_status == "REVOKED"
    assert consent_check(lead)[0] is False
    assert len(_events(db, lead, "WHATSAPP_CONSENT_REVOKED")) == 1


@pytest.mark.parametrize("text", [
    "Can you send the price list?", "We stopped using chicory blends last year",
    "The shop is near the bus stop", "end of month is better for an order",
])
def test_ordinary_words_containing_stop_are_not_an_opt_out(db, text):
    assert wc.is_whatsapp_opt_out(text) is False, text


def test_a_first_message_to_us_records_consent_bound_to_the_number_that_wrote(db):
    lead = _lead(db, whatsapp_number="9811100000")
    out = wc.capture_whatsapp_inbound(lead, db, sender="+91 98765 43210",
                                      text="Hi, please share your catalogue",
                                      message_id="wamid.1")

    assert out["recorded"] is True
    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_source == "WHATSAPP_INBOUND"
    assert lead.consent_phone == "9876543210", "the number that wrote, not one on file"
    ev = _events(db, lead, "WHATSAPP_CONSENT_RECORDED")
    assert len(ev) == 1 and "catalogue" in ev[0].payload["evidence"]


def test_an_unreadable_message_changes_nothing(db):
    """Without the words, "STOP" and "yes" look the same. Guessing yes is the
    bug this replaced."""
    lead = _lead(db)
    out = wc.capture_whatsapp_inbound(lead, db, sender="919876543210", text="")

    assert out["recorded"] is False
    assert (lead.consent_status or "UNKNOWN") != "EXPLICIT"
    assert len(_events(db, lead, "WHATSAPP_INBOUND_UNREAD")) == 1


def test_a_message_after_a_revocation_does_not_undo_it(db):
    lead = _lead(db, consent_status="REVOKED", consent_phone="9876543210")
    out = wc.capture_whatsapp_inbound(lead, db, sender="919876543210", text="hi")

    assert out["recorded"] is False
    assert lead.consent_status == "REVOKED"
    assert len(_events(db, lead, "WHATSAPP_INBOUND_AFTER_REVOKE")) == 1


# ── #32: a revocation holds at the moment of sending ─────────────────────────

def test_a_revoked_lead_that_replied_is_still_refused(db):
    """Replying "stop" makes status REPLIED. The old shortcut then waved the
    lead through as "replied to us — opt-in"."""
    lead = _lead(db, status="REPLIED", consent_status="REVOKED",
                 consent_phone="9876543210", whatsapp_number="9876543210")
    ok, why = consent_check(lead)
    assert ok is False and "revoked" in why.lower()


def test_an_email_reply_is_not_whatsapp_consent(db):
    lead = _lead(db, status="REPLIED", consent_status="UNKNOWN")
    assert consent_check(lead)[0] is False


def test_work_queued_before_a_revocation_cannot_send_after_it(db):
    """Cancelling queued rows is housekeeping. The guarantee is that the send
    itself re-reads consent, so anything that slipped past the cancel -- a
    request already in flight -- is still refused."""
    lead = _lead(db, consent_status="EXPLICIT", consent_phone="9876543210",
                 whatsapp_number="9876543210")
    wc.revoke(lead, db, evidence="STOP")
    r = send_whatsapp(lead, "catalogue", db=db)
    assert r.status == "blocked" and "revoked" in r.reason.lower()


# ── #30: one message, not two ────────────────────────────────────────────────

def _consented(db):
    return _lead(db, consent_status="EXPLICIT", consent_phone="9876543210",
                 whatsapp_number="9876543210")


def test_a_second_send_minutes_after_the_first_is_refused(db):
    lead = _consented(db)
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_SENT", channel="whatsapp",
                         occurred_at=datetime.utcnow() - timedelta(minutes=2)))
    db.commit()

    r = send_whatsapp(lead, "catalogue", db=db)
    assert r.status == "blocked" and "duplicate" in r.reason


def test_a_follow_up_days_later_is_not_mistaken_for_a_duplicate(db):
    lead = _consented(db)
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_SENT", channel="whatsapp",
                         occurred_at=datetime.utcnow() - timedelta(days=3)))
    db.commit()

    r = send_whatsapp(lead, "follow-up", db=db)
    assert "duplicate" not in (r.reason or "")
    assert r.status == "not_configured", "reached the transport, which is switched off"


# ── the webhook itself, over HTTP ────────────────────────────────────────────

@pytest.fixture
def webhook(db, monkeypatch):
    from app.api import endpoints
    from app.database.database import get_db

    monkeypatch.setenv("WHATSAPP_WEBHOOK_SECRET", "hook-secret")
    app = FastAPI()
    app.include_router(endpoints.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _post(client, payload):
    return client.post("/api/v1/whatsapp/webhook", json=payload,
                       headers={"x-webhook-secret": "hook-secret"})


def test_the_webhook_revokes_on_stop(webhook, db):
    lead = _lead(db, consent_status="EXPLICIT", consent_phone="9876543210",
                 whatsapp_number="9876543210")
    r = _post(webhook, {"type": "message", "from": "919876543210", "text": "STOP"})
    assert r.status_code == 200, r.text
    db.refresh(lead)
    assert lead.consent_status == "REVOKED"


@pytest.mark.parametrize("payload", [
    {"type": "message", "from": "919876543210", "text": "yes send the catalogue"},
    {"type": "message", "from": "919876543210", "message": {"text": "yes send the catalogue"}},
    {"type": "message", "from": "919876543210", "message": {"text": {"body": "yes send the catalogue"}}},
])
def test_the_webhook_reads_the_common_envelope_shapes(webhook, db, payload):
    lead = _lead(db)
    assert _post(webhook, payload).status_code == 200
    db.refresh(lead)
    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_phone == "9876543210"


def test_the_webhook_still_refuses_an_unsigned_caller(webhook, db):
    lead = _lead(db)
    r = webhook.post("/api/v1/whatsapp/webhook",
                     json={"type": "message", "from": "919876543210", "text": "yes"})
    assert r.status_code == 401
    db.refresh(lead)
    assert (lead.consent_status or "UNKNOWN") != "EXPLICIT"


# ── #33/#34: a send that may have gone out is never retried automatically ────

def _configured(monkeypatch):
    monkeypatch.setenv("AISENSY_ENABLED", "1")
    monkeypatch.setenv("AISENSY_API_KEY", "k")
    monkeypatch.setenv("WHATSAPP_TEMPLATE", "Purity Outreach")
    monkeypatch.setenv("WHATSAPP_CAMPAIGN_LIVE", "1")


def _raising(exc):
    class _C:
        def post(self, *a, **k):
            raise exc
    return _C()


def test_a_lost_answer_is_unknown_not_failed(monkeypatch):
    """A read timeout means the request was written; AiSensy may have
    accepted it. "failed" invites the retry that sends it twice."""
    import httpx
    from app.services import whatsapp_aisensy as transport

    _configured(monkeypatch)
    r = transport.send_template("9876543210", "Purity Outreach",
                                client=_raising(httpx.ReadTimeout("slow")))
    assert r.status == "unknown"
    assert "may have reached" in r.reason


def test_a_connection_that_never_opened_is_a_plain_failure(monkeypatch):
    import httpx
    from app.services import whatsapp_aisensy as transport

    _configured(monkeypatch)
    r = transport.send_template("9876543210", "Purity Outreach",
                                client=_raising(httpx.ConnectError("refused")))
    assert r.status == "failed"


def test_an_unknown_send_blocks_the_immediate_retry(db, monkeypatch):
    from app.services import whatsapp_aisensy as transport
    from app.services.whatsapp_aisensy import SendResult

    lead = _consented(db)
    monkeypatch.setattr(transport, "send_template",
                        lambda *a, **k: SendResult(status="unknown", reason="ReadTimeout"))
    first = send_whatsapp(lead, "catalogue", db=db)
    db.commit()
    assert first.status == "unknown"
    assert len(_events(db, lead, "WHATSAPP_SEND_UNCONFIRMED")) == 1

    calls = []
    monkeypatch.setattr(transport, "send_template",
                        lambda *a, **k: calls.append(1) or SendResult(status="sent"))
    again = send_whatsapp(lead, "catalogue", db=db)
    assert again.status == "blocked" and "duplicate" in again.reason
    assert calls == [], "the retry never reached the provider"


def test_smart_outreach_does_not_retry_an_unknown_send(db):
    from app.services import smart_outreach as so

    lead = _consented(db)
    db.add(so.OutreachTouch(lead_id=lead.id, channel="whatsapp", touch_type="SEND_CATALOGUE",
                            template_key="catalogue_request", status="UNKNOWN"))
    db.commit()
    assert so._proven_whatsapp_touches(db, lead.id, "catalogue_request"), (
        "an UNKNOWN touch must count as possibly sent")
