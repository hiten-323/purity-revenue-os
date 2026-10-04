"""Repeat-send incident 2026-10-03: AltSpace (45 emails) / FabHotel (37)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.models.models import B2BLead, WorkflowEvent, CallHistory, LeadInteraction
from app.services import smart_outreach as so
from app.services import email_repeat_guard as rg
from app.services.smart_outreach import OutreachProfile, OutreachTouch


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__, WorkflowEvent.__table__, OutreachProfile.__table__,
        OutreachTouch.__table__, CallHistory.__table__, LeadInteraction.__table__,
    ])
    return sessionmaker(bind=engine)()


def _lead(db, email="hello@altspaced.com", company="AltSpace"):
    lead = B2BLead(company=company, email=email, email_trust="TRUSTED",
                   email_confidence=70, email_source="FOUNDER_CALL",
                   coffee_buying_score=70, division="distributor", status="DISCOVERED")
    db.add(lead)
    db.commit()
    return lead


def _sent(db, lead, hours_ago, to=None):
    db.add(WorkflowEvent(lead_id=lead.id, event_type="EMAIL_SENT", actor="SMART_OUTREACH",
                         channel="email", payload={"to": to or lead.email, "message_id": "<m>"},
                         occurred_at=datetime.utcnow() - timedelta(hours=hours_ago)))
    db.commit()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("EMAIL_HOLD_LEAD_IDS", raising=False)
    monkeypatch.delenv("EMAIL_LEAD_MIN_GAP_HOURS", raising=False)


# ── guard ──────────────────────────────────────────────────────────────

def test_guard_blocks_second_email_inside_72h():
    db = _db(); lead = _lead(db); _sent(db, lead, 0.3)
    ok, why = rg.check(db, lead.id)
    assert not ok and why.startswith("HELD:")


def test_guard_allows_after_gap_and_first_email():
    db = _db(); lead = _lead(db)
    assert rg.check(db, lead.id)[0]
    _sent(db, lead, 73)
    assert rg.check(db, lead.id)[0]


def test_guard_allows_reply_inside_gap():
    db = _db(); lead = _lead(db); _sent(db, lead, 2)
    db.add(WorkflowEvent(lead_id=lead.id, event_type="EMAIL_REPLY_RECEIVED", actor="PROSPECT",
                         channel="email", payload={}, occurred_at=datetime.utcnow() - timedelta(hours=1)))
    db.commit()
    assert rg.check(db, lead.id)[0]


def test_hold_list_blocks(monkeypatch):
    db = _db(); lead = _lead(db)
    monkeypatch.setenv("EMAIL_HOLD_LEAD_IDS", f"999, {lead.id}")
    ok, why = rg.check(db, lead.id)
    assert not ok and "EMAIL_HOLD_LEAD_IDS" in why


def test_same_mailbox_other_lead_blocked():
    db = _db(); a = _lead(db, "x@hotel.com", "Hotel A"); b = _lead(db, "X@hotel.com", "Hotel B")
    _sent(db, a, 1)
    assert not rg.check(db, b.id, to_email=b.email)[0]


def test_reservation_counts_only_when_asked():
    db = _db(); lead = _lead(db)
    db.add(OutreachTouch(lead_id=lead.id, channel="email", touch_type="WARM_FOLLOW_UP",
                         template_key="reservation", status="SENDING", payload={}))
    db.commit()
    assert rg.check(db, lead.id)[0]                       # chokepoint view
    assert not rg.check(db, lead.id, include_reservations=True)[0]


# ── planner ────────────────────────────────────────────────────────────

def _verdict(monkeypatch, action):
    import app.services.decision_engine as de
    monkeypatch.setattr(de, "evaluate_next_action",
                        lambda lead, db: {"action": action, "reason": "LOCATION_CONFLICT"})


def test_draft_only_never_becomes_a_cadence_send(monkeypatch):
    db = _db(); lead = _lead(db)
    _verdict(monkeypatch, "DRAFT_ONLY")
    monkeypatch.setattr(so, "_sequence_state", lambda db, lead: {"active": False, "reason": "completed", "touches": 45})
    p = so.plan_touch(db, lead)
    assert p["execute"] is False


def test_send_verdict_with_completed_sequence_does_not_execute(monkeypatch):
    db = _db(); lead = _lead(db)
    _verdict(monkeypatch, "SEND")
    monkeypatch.setattr(so, "_sequence_state", lambda db, lead: {"active": False, "reason": "completed", "touches": 5})
    assert so.plan_touch(db, lead)["execute"] is False


def test_send_verdict_with_due_touch_still_sends(monkeypatch):
    db = _db(); lead = _lead(db)
    _verdict(monkeypatch, "SEND")
    monkeypatch.setattr(so, "_sequence_state", lambda db, lead: {"active": True, "ready": True, "reason": "in_sequence", "touches": 1, "next_touch": "nudge"})
    p = so.plan_touch(db, lead)
    assert p["execute"] is True and p["action"] == "WARM_FOLLOW_UP"


# ── execute_one idempotency ────────────────────────────────────────────

def _wire(monkeypatch, db, calls, on_send=None):
    import app.services.email_sender as es
    import app.services.outreach_learning as ol
    monkeypatch.setattr(so, "plan_touch", lambda db, lead, profile=None: {
        "action": "WARM_FOLLOW_UP", "channel": "email", "execute": True, "reason": "t"})
    monkeypatch.setattr(so, "_sequence_state", lambda db, lead: {"active": True, "ready": True, "touches": 1, "next_touch": "nudge"})
    monkeypatch.setattr(ol, "build_learning_context", lambda db, lead, profile: {})
    monkeypatch.setattr(es, "whatsapp_ask", lambda lead: "")

    def fake_send(email):
        calls.append(email.to_email)
        if on_send:
            on_send()
        email.status = "sent"; email.message_id = f"<m{len(calls)}>"; email.error = ""
        return email
    monkeypatch.setattr(es, "send_email", fake_send)


def test_execute_one_sends_once_across_cycles(monkeypatch):
    db = _db(); lead = _lead(db); calls = []
    _wire(monkeypatch, db, calls)
    r1 = so.execute_one(db, lead)
    r2 = so.execute_one(db, lead)
    r3 = so.execute_one(db, lead)
    assert r1["status"] == "SENT"
    assert r2["status"] == "SKIPPED" and r3["status"] == "SKIPPED"
    assert len(calls) == 1
    # reservation replaced by the durable SENT record
    assert db.query(OutreachTouch).filter_by(status="SENDING").count() == 0
    sent = db.query(OutreachTouch).filter_by(status="SENT").one()
    assert sent.payload["step_key"] == "WARM_FOLLOW_UP:nudge:1"


def test_reservation_is_committed_before_smtp(monkeypatch):
    db = _db(); lead = _lead(db); calls = []
    seen = {}
    other = sessionmaker(bind=db.get_bind())

    def check():
        s = other()
        seen["n"] = s.query(OutreachTouch).filter_by(status="SENDING").count()
        s.close()
    _wire(monkeypatch, db, calls, on_send=check)
    so.execute_one(db, lead)
    assert seen["n"] == 1


def test_leftover_reservation_blocks_resend(monkeypatch):
    """Crash between SMTP success and the record: next cycle must not resend."""
    db = _db(); lead = _lead(db); calls = []
    db.add(OutreachTouch(lead_id=lead.id, channel="email", touch_type="WARM_FOLLOW_UP",
                         template_key="reservation", status="SENDING",
                         payload={"step_key": "WARM_FOLLOW_UP:nudge:1"}))
    db.commit()
    _wire(monkeypatch, db, calls)
    r = so.execute_one(db, lead)
    assert r["status"] == "SKIPPED" and calls == []


# ── chokepoint ─────────────────────────────────────────────────────────

def test_send_email_chokepoint_refuses_repeat(monkeypatch):
    """Every path (queue drain, endpoints) passes send_email; it must refuse too."""
    import app.services.email_sender as es
    import app.database.database as dbm
    import smtplib
    db = _db(); lead = _lead(db); _sent(db, lead, 0.5)
    lead.email_verification_status = "FOUNDER_CALL_PROVIDED"; db.commit()
    lid = lead.id
    monkeypatch.setattr(dbm, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setenv("EMAIL_HOLD_LEAD_IDS", "")
    import app.services.business_hours as bh
    monkeypatch.setattr(bh, "is_open", lambda *a, **k: True)
    monkeypatch.setattr(es, "SENDER_PASSWORD", "x")
    monkeypatch.setattr(es, "domain_is_deliverable", lambda *a, **k: True)
    import app.services.trust_promoter as tp
    monkeypatch.setattr(tp, "may_send", lambda lead: (True, "TRUSTED"))
    import app.services.email_verifier as ev
    monkeypatch.setattr(ev, "verify_email", lambda *a, **k: {"status": "VERIFIED"})
    import app.services.deliverability as dl
    monkeypatch.setattr(dl, "check_send_allowed", lambda db: SimpleNamespace(allowed=True, reason=""))
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SMTP reached")))
    e = es.OutreachEmail(to_email=lead.email, to_name="", company="AltSpace",
                         subject="s", body_text="b", lead_id=lid)
    r = es.send_email(e)
    assert r.status == "failed" and "at most one email" in r.error
