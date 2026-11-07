"""3 Nov 2026: shared harness for the WhatsApp tests on the Klaviyo path.

Klaviyo is the only WhatsApp transport now (AiSensy retired). These helpers
let a test drive the REAL send chain -- consent_check -> provider() ->
_send_via_klaviyo -> pacer.eligible / call_consent / OneAtATime / slot_free
-> whatsapp_klaviyo.emit -- with only the network edge replaced:

  * httpx.post (Klaviyo's /api/events) is a recorder returning a scripted reply
  * ensure_subscribed (Klaviyo profile lookup) is stubbed to "subscribed"
  * every other way out (httpx transports, urllib, smtplib, sockets to a
    non-loopback host) raises, so a test that forgets a mock fails loudly
    instead of reaching the real Klaviyo account
  * dotenv_values returns {} so config never falls back to a real backend/.env
  * the drafts store, pacer lock, block/strike lists live under tmp_path

No real send, dial or network call can happen from a test using this.
"""
from __future__ import annotations

import socket
import smtplib
import urllib.request
from datetime import datetime, timedelta

import httpx

from app.models.models import B2BLead, WorkflowEvent

FLAG_VARS = ("WHATSAPP_PROVIDER", "KLAVIYO_WA_ENABLED", "KLAVIYO_WA_FLOW_LIVE", "KLAVIYO_PRIVATE_KEY")
_TEST_ALLOW = set()


class NetworkTouched(AssertionError):
    pass


def _boom(*a, **k):
    raise NetworkTouched("a WhatsApp test tried to reach the network")


def no_network(monkeypatch):
    """Tripwires on every outbound path; loopback (TestClient) is untouched."""
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(smtplib, "SMTP", _boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _boom)
    real_connect = socket.socket.connect

    def guarded(self, addr, *a, **k):
        host = addr[0] if isinstance(addr, tuple) else addr
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost") and "/" not in host:
            _boom()
        return real_connect(self, addr, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **k: {})


def klaviyo_flags(monkeypatch, *, enabled="1", flow_live="1", key="pk_test_dummy", provider="klaviyo"):
    """Set every flag explicitly so nothing is read from a real .env."""
    no_network(monkeypatch)
    monkeypatch.setenv("WHATSAPP_PROVIDER", provider)
    monkeypatch.setenv("KLAVIYO_WA_ENABLED", enabled)
    monkeypatch.setenv("KLAVIYO_WA_FLOW_LIVE", flow_live)
    monkeypatch.setenv("KLAVIYO_PRIVATE_KEY", key)
    monkeypatch.setenv("AISENSY_ENABLED", "0")
    monkeypatch.setenv("KLAVIYO_WA_SYNC_ENABLED", "0")


class KlaviyoRecorder:
    """Stands in for Klaviyo's /api/events. `script` is a list of replies:
    an int status code, or an Exception instance to raise (transport error)."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "body": json})
        nxt = self.script.pop(0) if self.script else 202
        if isinstance(nxt, BaseException):
            raise nxt
        return httpx.Response(nxt, request=httpx.Request("POST", url))


def klaviyo_on(monkeypatch, tmp_path, script=None, subscribed=True):
    """Klaviyo path fully configured with a dummy key and every edge mocked.
    Returns the recorder; recorder.calls is what 'reached Klaviyo'."""
    from app.services import whatsapp_drafts as wd, whatsapp_klaviyo as k, whatsapp_pacer as p

    klaviyo_flags(monkeypatch)
    monkeypatch.setattr(wd, "STORE", tmp_path / "wa_drafts.json")
    monkeypatch.setattr(p, "LOCK", str(tmp_path / "wa_pacer.lock"))
    monkeypatch.setattr(p, "BLOCK_FILES", [])
    monkeypatch.setattr(p, "STRIKE_FILES", [])
    monkeypatch.setattr(p, "in_hours", lambda now=None: True)   # the clock is not under test
    monkeypatch.setattr(k, "ensure_subscribed",
                        lambda lead, phone, consent, timeout=15.0:
                        (True, "subscribed") if subscribed else (False, "not subscribed in Klaviyo; not sent"))
    monkeypatch.setattr(k, "wa_status",
                        lambda phone, timeout=15.0:
                        ("profile", True, "SUBSCRIBED") if subscribed else ("", False, "UNSUBSCRIBED"))
    from app.services import gate1_holds as _gate
    monkeypatch.setattr(_gate, "provider_subscription_allows",
                        lambda phone: (True, "SUBSCRIBED") if subscribed else (False, "UNSUBSCRIBED"))
    rec = KlaviyoRecorder(script)
    monkeypatch.setattr(k.httpx, "post", rec.post)
    _TEST_ALLOW.clear()
    monkeypatch.setattr(
        "app.services.outbound_cohort.allow_list_ids",
        lambda: frozenset(_TEST_ALLOW),
    )
    return rec


def ai_call_consented(db, *, phone="9876543210", call_ref="call-test-1", minutes_ago=30, **kw):
    """A lead whose WhatsApp opt-in was recorded on an AI call, the way
    whatsapp_consent.record writes it (status, source, bound number, time, event)."""
    at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    kw.setdefault("company", "Klaviyo Path Traders")
    lead = B2BLead(phone=phone, whatsapp_number=phone, consent_status="EXPLICIT",
                   consent_source="AI_CALL_WHATSAPP_REQUEST", consent_phone=phone,
                   consent_timestamp=at, **kw)
    db.add(lead)
    db.commit()
    import hashlib
    evidence = f"asked for the catalogue on WhatsApp | call_ref: {call_ref}"
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_CONSENT_RECORDED", actor="SYSTEM",
                         channel="phone", occurred_at=at,
                         payload={"source": "AI_CALL_WHATSAPP_REQUEST",
                                  "evidence": evidence,
                                  "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest()}))
    db.commit()
    _TEST_ALLOW.add(lead.id)
    return lead


def approved_draft(lead_id, template_key="pb_catalogue", name="Ram"):
    from app.services import whatsapp_drafts as wd
    did = wd.create(lead_id, template_key, {"name": name})   # reuses an open draft for the lead
    if wd.all_drafts()[did]["status"] == "PENDING":
        wd.approve(did, "Hiten")
    return did


def wa_events(db, lead, *kinds):
    q = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id)
    if kinds:
        q = q.filter(WorkflowEvent.event_type.in_(kinds))
    return q.all()
