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


def test_later_unbound_and_uncleared_provider_block_reject_before_lookup(monkeypatch):
    from datetime import datetime
    db, engine = _db()
    try:
        looked = []
        monkeypatch.setattr(g, "provider_subscription_allows",
                            lambda phone: looked.append(phone) or (True, "SUBSCRIBED"))
        phone = "+919876543210"
        evidence = "yes, send the catalogue on this number"
        import hashlib
        digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        unbound = _lead(db, company="Unbound Cafe", phone=phone, whatsapp_number=phone,
                        consent_phone=phone, consent_status="EXPLICIT", email=None,
                        email_trust="UNKNOWN", email_verified=False)
        blocked = _lead(db, company="Blocked Cafe", phone=phone, whatsapp_number=phone,
                        consent_phone=phone, consent_status="EXPLICIT", email=None,
                        email_trust="UNKNOWN", email_verified=False)
        clear = _lead(db, company="Clear Cafe", phone=phone, whatsapp_number=phone,
                      consent_phone=phone, consent_status="EXPLICIT", email=None,
                      email_trust="UNKNOWN", email_verified=False)
        recorded_at = datetime(2026, 10, 15, 10, 0, 0)
        for lead in (unbound, blocked, clear):
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="WHATSAPP_CONSENT_RECORDED", actor="SYSTEM",
                channel="whatsapp", occurred_at=recorded_at,
                payload={"evidence": evidence, "evidence_sha256": digest}))
        db.add(WorkflowEvent(
            lead_id=unbound.id, event_type="WHATSAPP_CONSENT_UNBOUND", actor="SYSTEM",
            channel="whatsapp", occurred_at=datetime(2026, 11, 2, 5, 0, 0),
            payload={"note": "later unbound"}))
        db.add(OutboundWhatsApp(lead_id=blocked.id, status="BLOCKED_PROVIDER_CONSENT",
                                delivered_at=None))
        db.commit()

        assert g.evidence_refusal(unbound, db) == g.UNBOUND_REASON
        assert g.evidence_refusal(blocked, db) == g.BLOCKED_PROVIDER_CONSENT_REASON
        assert looked == []
        assert db.query(OutboundWhatsApp).filter_by(id=db.query(OutboundWhatsApp).one().id).one().status == "BLOCKED_PROVIDER_CONSENT"
        assert unbound.consent_status == "EXPLICIT"
        assert g.evidence_refusal(clear, db) is None
        assert len(looked) == 1
    finally:
        db.close()
        engine.dispose()


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


_TEN = (
    "AUTO_WARM_ENABLED", "SMART_OUTREACH_ENABLED", "AUTO_OUTREACH_ENABLED",
    "AI_CALLING_ENABLED", "AUTO_AI_CALLING_ENABLED", "AISENSY_ENABLED",
    "EMAIL_SENDING_ENABLED", "KLAVIYO_WA_ENABLED", "KLAVIYO_WA_FLOW_LIVE",
    "WHATSAPP_PACER_ENABLED",
)


def _silence_providers(monkeypatch, called):
    monkeypatch.setattr(g, "provider_subscription_allows",
                        lambda phone: called.append("lookup") or (True, "SUBSCRIBED"))
    monkeypatch.setattr("app.services.whatsapp_klaviyo.wa_status",
                        lambda *a, **k: called.append("wa_status") or ("", True, "SUBSCRIBED"))
    monkeypatch.setattr("app.services.whatsapp_klaviyo.emit",
                        lambda *a, **k: called.append("emit"))
    monkeypatch.setattr("app.services.whatsapp_aisensy.send_template",
                        lambda *a, **k: called.append("aisensy"))
    monkeypatch.setattr("app.services.business_hours.is_open", lambda: True)


def test_zero_flags_return_before_sms_sample_email_or_draft(monkeypatch):
    for name in _TEN:
        monkeypatch.setenv(name, "0")
    called = []
    _silence_providers(monkeypatch, called)
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: called.append("smtp"))
    assert g.provider_armed("sms") is False
    assert g.provider_armed("sample") is False
    assert g.provider_armed("draft") is False
    assert g.provider_armed("email") is False
    assert g.provider_armed("whatsapp") is False
    assert g.provider_armed("call") is False
    from app.api.endpoints import dispatch_sample
    sample = dispatch_sample({"company": "Cafe", "contact_name": "A", "phone": "000"})
    assert sample["status"] == "blocked" and sample["sent"] is False
    from app.services.email_sender import OutreachEmail, send_email
    held = send_email(OutreachEmail(
        to_email="buyer@example.in", to_name="A", company="Cafe",
        subject="Hello", body_text="Catalogue", lead_id=None))
    assert held.status != "sent"
    assert called == []


def test_flag_on_dispatch_rejects_holds_before_any_provider(monkeypatch):
    for name in _TEN:
        monkeypatch.setenv(name, "1")
    called = []
    _silence_providers(monkeypatch, called)
    from app.services import whatsapp_drafts, whatsapp_klaviyo, whatsapp_pacer as pacer
    from app.services.whatsapp_sender import send_whatsapp
    monkeypatch.setattr(whatsapp_drafts, "approved_for",
                        lambda draft_id, lead_id: {"template_key": "t", "params": {}})
    monkeypatch.setattr(pacer, "eligible", lambda db, lead: (True, "ok"))
    monkeypatch.setattr(pacer, "slot_free", lambda db: (True, "ok"))

    class _Lock:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(pacer, "OneAtATime", _Lock)
    monkeypatch.setattr(whatsapp_klaviyo, "call_consent",
                        lambda db, lead: (SimpleNamespace(ok=True), "ok"))
    monkeypatch.setenv("WHATSAPP_PROVIDER", "klaviyo")
    monkeypatch.setenv("KLAVIYO_PRIVATE_KEY", "test-key")
    monkeypatch.setattr("app.services.outbound_cohort.allow_list_ids", lambda: frozenset({9}))

    phone = "+919876543210"
    ready = dict(phone=phone, whatsapp_number=phone, consent_phone=phone,
                 consent_status="EXPLICIT", do_not_call=False,
                 contact_status="CONTACTABLE", evidence_sha256="ab" * 32)
    cases = {
        "contact": ready | {"contact_status": "DO_NOT_CONTACT"},
        "dnc": ready | {"do_not_call": True},
        "closed": ready | {"status": "CLOSED_UNRECOVERABLE"},
        "revoked": ready | {"consent_status": "REVOKED"},
        "opt_out": ready | {"consent_status": "OPT_OUT"},
        "wrong": ready | {"status": "WRONG_NUMBER"},
        "unknown": ready | {"consent_status": "UNKNOWN"},
        "unbound": ready | {"later_whatsapp_consent_unbound": True},
        "provider_block": ready | {"blocked_provider_consent": True},
        "no_event": ready | {"blocked_no_provider_event": True},
    }
    expected = {
        "contact": g.DRAFT_VOID_REASON,
        "dnc": "do_not_call",
        "closed": "status=CLOSED_UNRECOVERABLE",
        "revoked": "consent_status=REVOKED",
        "opt_out": "consent_status=OPT_OUT",
        "wrong": "status=WRONG_NUMBER",
        "unknown": "consent_status=UNKNOWN",
        "unbound": g.UNBOUND_REASON,
        "provider_block": g.BLOCKED_PROVIDER_CONSENT_REASON,
        "no_event": g.BLOCKED_NO_PROVIDER_EVENT_REASON,
    }
    reaches_evidence = {"contact", "closed", "wrong", "unbound", "provider_block", "no_event"}
    for name, fields in cases.items():
        lead = _ready(**fields)
        reason = g.evidence_refusal(lead)
        assert expected[name] in reason, reason
        result = send_whatsapp(lead, "hello", db=object(), approved_draft_id="draft")
        assert result.status == "blocked"
        if name in reaches_evidence:
            assert expected[name] in result.reason
    assert called == []
    assert g.provider_armed("sms") is False
    assert g.provider_armed("sample") is False


def test_flag_on_fixture_rows_reject_before_lookup(monkeypatch):
    for name in _TEN:
        monkeypatch.setenv(name, "1")
    looked = []
    monkeypatch.setattr(g, "provider_subscription_allows",
                        lambda phone: looked.append("lookup") or (True, "SUBSCRIBED"))
    db, engine = _db()
    try:
        phone = "+919876543210"
        import hashlib
        digest = hashlib.sha256(b"yes, send the catalogue on this number").hexdigest()
        recorded = datetime(2026, 10, 15, 10, 0, 0)

        def add(company, **kw):
            lead = _lead(db, company=company, phone=phone, whatsapp_number=phone,
                         consent_phone=phone, consent_status="EXPLICIT", email=None,
                         email_trust="UNKNOWN", email_verified=False, **kw)
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="WHATSAPP_CONSENT_RECORDED", actor="SYSTEM",
                channel="whatsapp", occurred_at=recorded,
                payload={"evidence": "yes", "evidence_sha256": digest}))
            return lead

        contact = add("Contact Hold Cafe", contact_status="DO_NOT_CONTACT")
        closed = add("Closed Hold Cafe", status="CLOSED_UNRECOVERABLE")
        unbound = add("Unbound Hold Cafe")
        provider_blocked = add("Provider Block Cafe")
        no_event = add("No Event Cafe")
        db.add(WorkflowEvent(
            lead_id=unbound.id, event_type="WHATSAPP_CONSENT_UNBOUND", actor="SYSTEM",
            channel="whatsapp", occurred_at=datetime(2026, 11, 2, 5, 0, 0),
            payload={"note": "later unbound"}))
        db.add(OutboundWhatsApp(lead_id=provider_blocked.id, status="BLOCKED_PROVIDER_CONSENT",
                                delivered_at=None))
        blocked_row = OutboundWhatsApp(lead_id=no_event.id, status="BLOCKED_NO_PROVIDER_EVENT",
                                       delivered_at=None)
        db.add(blocked_row)
        db.commit()
        assert g.DRAFT_VOID_REASON in g.evidence_refusal(contact, db)
        assert "CLOSED_UNRECOVERABLE" in g.evidence_refusal(closed, db)
        assert g.evidence_refusal(unbound, db) == g.UNBOUND_REASON
        assert g.evidence_refusal(provider_blocked, db) == g.BLOCKED_PROVIDER_CONSENT_REASON
        assert g.evidence_refusal(no_event, db) == g.BLOCKED_NO_PROVIDER_EVENT_REASON
        assert g.reminder_may_dispatch(contact, "whatsapp", "SCHEDULED", db)[0] is False
        assert g.reminder_may_dispatch(no_event, "whatsapp", "SCHEDULED", db)[0] is False
        assert looked == []
        db.refresh(blocked_row)
        assert blocked_row.status == "BLOCKED_NO_PROVIDER_EVENT"
        assert blocked_row.delivered_at is None
    finally:
        db.close()
        engine.dispose()


def test_warm_nudge_does_not_draft_when_flags_are_off_or_the_lead_is_held(monkeypatch, tmp_path):
    from app.models.models import EmailDraft
    from app.services import warm_followup as wf
    from app.services import whatsapp_drafts
    monkeypatch.setattr(whatsapp_drafts, "STORE", tmp_path / "wa.json")
    called = []
    _silence_providers(monkeypatch, called)
    db, engine = _db()
    try:
        lead = _lead(db, company="Nudge Hold Cafe", contact_status="DO_NOT_CONTACT",
                     consent_status="EXPLICIT", do_not_call=False,
                     phone="+919876543210", whatsapp_number="+919876543210",
                     consent_phone="+919876543210", email="nudge@example.in")
        sent_at = datetime(2026, 10, 1, 8, 0, 0)
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="WARM_FOLLOWUP_STARTED", actor="SYSTEM",
            channel="email", occurred_at=sent_at, payload={"class": "MAYBE"}))
        db.add(EmailDraft(lead_id=lead.id, follow_up_type="warm_followup",
                          subject="s", body="b", status="SENT", sent_at=sent_at,
                          recipient=lead.email))
        db.commit()
        for name in _TEN:
            monkeypatch.setenv(name, "0")
        monkeypatch.setenv("WARM_FOLLOWUP_ENABLED", "1")
        skipped = wf.tick(db, now=sent_at + __import__("datetime").timedelta(days=4))
        assert skipped["skipped"] == "outbound flags off"
        assert skipped["nudges"] == []
        monkeypatch.setenv("EMAIL_SENDING_ENABLED", "1")
        armed = wf.tick(db, now=sent_at + __import__("datetime").timedelta(days=4))
        assert armed["nudges"] == []
        assert db.query(WorkflowEvent).filter(
            WorkflowEvent.event_type == "WARM_FOLLOWUP_NUDGE_DRAFTED").count() == 0
        assert called == []
    finally:
        db.close()
        engine.dispose()


def test_place_call_rejects_each_hold_while_the_calling_flag_is_on(monkeypatch):
    from app.services import founder_call_pipeline as fcp
    from app.services import voice_router
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.delenv("AI_CALLING_KILL_SWITCH", raising=False)
    monkeypatch.setattr(fcp, "call_hours_open", lambda lead=None: (True, "test"))
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "ok"))
    called = []
    monkeypatch.setattr(voice_router, "_adapter", lambda *a, **k: called.append("dial"))
    monkeypatch.setattr(g, "provider_subscription_allows",
                        lambda phone: called.append("lookup") or (True, "SUBSCRIBED"))
    phone = "+919876543210"
    base = dict(id=41, phone=phone, company="Cafe", contact_name="A", city="Mohali",
                segment="cafe", consent_status="EXPLICIT", do_not_call=False,
                contact_status="CONTACTABLE", status="NEW", phone_trust="",
                whatsapp_number=phone, consent_phone=phone, evidence_sha256="ab" * 32,
                dnc_reason=None)
    variants = [
        base | {"contact_status": "DO_NOT_CONTACT"},
        base | {"do_not_call": True},
        base | {"status": "CLOSED_UNRECOVERABLE"},
        base | {"consent_status": "REVOKED"},
        base | {"consent_status": "OPT_OUT"},
        base | {"status": "WRONG_NUMBER"},
        base | {"consent_status": "UNKNOWN"},
        base | {"later_whatsapp_consent_unbound": True},
        base | {"blocked_provider_consent": True},
        base | {"blocked_no_provider_event": True},
    ]
    for fields in variants:
        result = voice_router.place_call(SimpleNamespace(**fields), dry_run=False)
        assert result.placed is False
    assert called == []
