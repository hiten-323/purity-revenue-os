"""Startup trust sweep and dispatch must leave Gate 1 holds in place."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.models.models import (
    B2BLead, Base, OutboundWhatsApp, OutreachReminder, WorkflowEvent,
)
from app.services import contact_trust, gate1_holds as g
from conftest import memory_engine


def _db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _lead(db, **kw):
    base = dict(company="Hold Cafe " + str(kw.get("email", "x")), city="Mohali", status="DISCOVERED",
                contact_status="CONTACTABLE", consent_status="UNKNOWN",
                do_not_call=False, email="held@example.in", email_trust="VERIFIED",
                email_verified=True, phone="+919811122233")
    base.update(kw)
    lead = B2BLead(**base)
    db.add(lead)
    db.commit()
    return lead


def test_sweep_leaves_gate1_holds_untouched_even_when_reverify_would_purge(monkeypatch):
    db, engine = _db()
    try:
        closed = _lead(db, status="CLOSED_UNRECOVERABLE", contact_status="QUARANTINED",
                       do_not_call=True, dnc_reason="ORPHAN_IDENTITY_MISSING",
                       consent_status="UNKNOWN", email="closed@example.in",
                       phone=None, whatsapp_number=None, consent_phone=None)
        dnc = _lead(db, do_not_call=True, dnc_reason="asked", consent_status="REVOKED",
                    status="WRONG_NUMBER", contact_status="DO_NOT_CONTACT",
                    email="dnc@example.in")
        unknown = _lead(db, consent_status="UNKNOWN", contact_status="DO_NOT_CONTACT",
                        email="unknown@example.in", email_trust="UNKNOWN",
                        email_verified=True)
        wa_blocked = OutboundWhatsApp(lead_id=dnc.id, status="BLOCKED_NO_PROVIDER_EVENT",
                                      delivered_at=None, whatsapp_response="{}")
        wa_dnc = OutboundWhatsApp(lead_id=dnc.id, status="BLOCKED_DNC", delivered_at=None)
        reminder = OutreachReminder(lead_id=unknown.id, channel="whatsapp",
                                    due_at=datetime(2026, 11, 6, 7, 0, 0),
                                    status="REJECTED_NO_CONSENT",
                                    reason="consent UNKNOWN")
        db.add_all([wa_blocked, wa_dnc, reminder])
        db.commit()
        db.execute(text("""CREATE TABLE klaviyo_delivery_quarantine (
            id INTEGER PRIMARY KEY, provider_event_id TEXT, flow_id TEXT,
            event_type TEXT, event_at TEXT, reason TEXT, recorded_at TEXT,
            resolution_status TEXT)"""))
        db.execute(text("""INSERT INTO klaviyo_delivery_quarantine
            (provider_event_id, flow_id, event_type, event_at, reason, recorded_at, resolution_status)
            VALUES ('evt-locked', 'TUrAGc', 'relayed', '2026-11-02T00:00:00Z',
                    'no_local_send_in_24h_window', '2026-11-06T00:00:00Z',
                    'PERMANENTLY_UNMATCHED')"""))
        db.commit()

        def _boom(*_a, **_k):
            raise AssertionError("sweep called the live verifier for a held lead")

        monkeypatch.setattr("app.services.email_verifier.verify_email", _boom)

        before = {
            "closed": (closed.status, closed.contact_status, closed.do_not_call,
                       closed.consent_status, closed.email, closed.phone),
            "dnc": (dnc.status, dnc.contact_status, dnc.do_not_call, dnc.consent_status,
                    dnc.dnc_reason, dnc.email),
            "unknown": (unknown.contact_status, unknown.consent_status, unknown.email,
                        unknown.email_trust, unknown.email_verified),
        }
        contact_trust.sweep(db, reverify=False)
        contact_trust.sweep(db, reverify=True)
        for lead in (closed, dnc, unknown):
            db.refresh(lead)
        db.refresh(wa_blocked)
        db.refresh(wa_dnc)
        db.refresh(reminder)

        assert (closed.status, closed.contact_status, closed.do_not_call,
                closed.consent_status, closed.email, closed.phone) == before["closed"]
        assert (dnc.status, dnc.contact_status, dnc.do_not_call, dnc.consent_status,
                dnc.dnc_reason, dnc.email) == before["dnc"]
        assert (unknown.contact_status, unknown.consent_status, unknown.email,
                unknown.email_trust, bool(unknown.email_verified)) == (
                    "DO_NOT_CONTACT", "UNKNOWN", "unknown@example.in", "UNKNOWN", True)
        assert wa_blocked.status == "BLOCKED_NO_PROVIDER_EVENT"
        assert wa_blocked.delivered_at is None
        assert wa_dnc.status == "BLOCKED_DNC"
        assert reminder.status == "REJECTED_NO_CONSENT"
        row = db.execute(text(
            "SELECT resolution_status, reason FROM klaviyo_delivery_quarantine "
            "WHERE provider_event_id='evt-locked'")).one()
        assert row[0] == "PERMANENTLY_UNMATCHED"
        assert row[1] == "no_local_send_in_24h_window"
        assert db.query(WorkflowEvent).filter(
            WorkflowEvent.event_type == "CONTACT_PURGED").count() == 0
    finally:
        db.close()
        engine.dispose()


def _ready(**kw):
    base = dict(id=9, company="Review Cafe", phone="+919876543210",
                whatsapp_number="+91-98765-43210", consent_phone="+919876543210",
                consent_status="EXPLICIT", do_not_call=False, status="DISCOVERED",
                contact_status="CONTACTABLE", evidence_sha256="ab" * 32)
    base.update(kw)
    return SimpleNamespace(**base)


def test_contactable_requires_every_dispatch_check():
    gaps = g.evidence_gaps(_ready(), evidence_hash_present=True, provider_allows=True)
    assert gaps == []
    missing = g.evidence_gaps(
        _ready(), evidence_hash_present=True, provider_allows=False, provider_detail="NO_PROFILE")
    assert missing == ["provider subscription does not allow the send: NO_PROFILE"]
    assert g.consent_phone_matches_destination(_ready()) is True
    assert g.consent_phone_matches_destination(_ready(consent_phone="+919999999999")) is False


def test_flags_off_do_not_reach_the_provider(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PROVIDER", "klaviyo")
    monkeypatch.setenv("KLAVIYO_WA_ENABLED", "0")
    monkeypatch.setenv("KLAVIYO_WA_FLOW_LIVE", "0")
    called = []
    monkeypatch.setattr("app.services.whatsapp_klaviyo.wa_status",
                        lambda *a, **k: called.append("lookup") or ("", False, "NO_PROFILE"))
    monkeypatch.setattr("app.services.whatsapp_klaviyo.emit",
                        lambda *a, **k: called.append("emit"))
    from app.services.whatsapp_sender import send_whatsapp
    result = send_whatsapp(_ready(), "", db=object(), approved_draft_id="draft")
    assert result.status == "not_configured"
    assert called == []


def test_armed_dispatch_rejects_when_provider_subscription_does_not_allow(monkeypatch):
    monkeypatch.setattr(g, "provider_subscription_allows", lambda phone: (False, "NO_PROFILE"))
    reason = g.evidence_refusal(_ready())
    assert reason == "provider subscription does not allow the send: NO_PROFILE"
    monkeypatch.setattr(g, "provider_subscription_allows", lambda phone: (True, "SUBSCRIBED"))
    assert g.evidence_refusal(_ready()) is None


def test_place_call_rejects_unknown_consent_while_the_calling_flag_is_on(monkeypatch):
    from app.services import founder_call_pipeline as fcp
    from app.services import voice_router

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.delenv("AI_CALLING_KILL_SWITCH", raising=False)
    monkeypatch.setattr(fcp, "call_hours_open", lambda lead=None: (True, "test"))
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "ok"))

    def _boom(*_a, **_k):
        raise AssertionError("provider dial was attempted")

    monkeypatch.setattr(voice_router, "_adapter", _boom)
    lead = SimpleNamespace(id=3, phone="+919811122233", company="Cafe", contact_name="A",
                           city="Mohali", segment="cafe", consent_status="UNKNOWN",
                           do_not_call=False, contact_status="CONTACTABLE", status="NEW",
                           phone_trust="", contact_status_reason="", dnc_reason=None,
                           whatsapp_number=None, consent_phone=None)
    result = voice_router.place_call(lead, dry_run=False)
    assert result.placed is False
    assert "consent_status=UNKNOWN" in result.error
