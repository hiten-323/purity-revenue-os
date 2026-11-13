"""Warm follow-up after an AI call (29 Oct 2026, Hiten). Drafts only; never
auto-approved; backup/second-brand framing; founder-call ask -> no message."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, EmailDraft, WorkflowEvent, WorkflowExecution
from app.services import founder_call_pipeline as p
from app.services import sequence_engine as se
from app.services import warm_followup as wf
from conftest import memory_engine


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app.services import whatsapp_drafts
    monkeypatch.setattr(whatsapp_drafts, "STORE", tmp_path / "wa.json")
    monkeypatch.setenv("PURITY_BEANS_CATALOGUE_URL", "https://p3online.in/catalogue")
    monkeypatch.delenv("WARM_FOLLOWUP_ENABLED", raising=False)
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


def _lead(db, **kw):
    kw.setdefault("id", 550)
    kw.setdefault("email_trust", "TRUSTED")
    kw.setdefault("email_confidence", 80)
    kw.setdefault("email_source", "WEBSITE")
    kw.setdefault("email_mx_valid", 1)
    kw.setdefault("email_verified", 1)
    kw.setdefault("email_verification_status", "VALID")
    lead = B2BLead(company=kw.pop("company", "Sharma Traders"),
                   phone=kw.pop("phone", "9855593323"),
                   email=kw.pop("email", "owner@sharma.example"),
                   contact_name=kw.pop("contact_name", "Rakesh Sharma"),
                   segment=kw.pop("segment", "distributor"), **kw)
    db.add(lead)
    db.commit()
    return lead


def _types(db, lead):
    return [e.event_type for e in db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id)]


def _warm_drafts(db, lead):
    return db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id,
                                       EmailDraft.follow_up_type.in_(wf.FOLLOWUP_TYPES)).all()


# ---------------------------------------------------------------- classify
@pytest.mark.parametrize("key,kw,expected", [
    ("OPT_OUT", {}, wf.NO_STOP),
    ("NOT_INTERESTED", {}, wf.NO_STOP),
    ("NOT_INTERESTED", {"transcript": "abhi nahi, next month call karna"}, wf.NOT_NOW),
    ("REJECTED", {"details": {"objection": "TIMING"}}, wf.NOT_NOW),
    ("INTERESTED", {"interest": "HOT"}, wf.INTERESTED),
    ("INTERESTED", {"interest": "COOL"}, wf.MAYBE),
    ("INTERESTED", {"details": {"objection": "EXISTING_SUPPLIER"}}, wf.MAYBE),
    ("WHATSAPP_OPT_IN", {"transcript": "maybe, we will think about it"}, wf.MAYBE),
    ("WHATSAPP_OPT_IN", {}, wf.INTERESTED),
    ("CALLBACK_REQUESTED", {}, wf.INTERESTED),
    ("NO_ANSWER", {}, None),
    ("WRONG_NUMBER", {}, None),
    ("OTHER", {}, None),
])
def test_classify(key, kw, expected):
    assert wf.classify(key, **kw) == expected


# ---------------------------------------------------------------- interested / maybe
def test_interested_marks_warm_stops_sequence_and_drafts_one_unapproved_email(db):
    lead = _lead(db)
    cold = EmailDraft(lead_id=lead.id, follow_up_type="nudge", subject="s", body="b", status="DRAFT")
    db.add(cold); db.commit()
    p.record_ai_outcome(lead, db, "INTERESTED", summary="Wants price list and margin",
                        transcript="What is the price and what margin do you give?")
    db.commit()
    assert lead.intent_tier == "Warm"
    assert "SEQUENCE_STOPPED_WARM" in _types(db, lead)
    assert se.state(lead, db)["reason"] == "warm_handoff"
    db.refresh(cold)
    assert cold.status == "CANCELLED_WARM"
    ds = _warm_drafts(db, lead)
    assert len(ds) == 1
    d = ds[0]
    assert d.status == "DRAFT" and d.approved_at is None
    assert "never auto-approved" in d.reason
    assert "backup or second instant coffee brand" in d.body
    assert "up to 30% (25% trade discount + 5% cash discount)" in d.body
    assert wf.FACTORY_LINE in d.body
    assert "https://p3online.in/catalogue" in d.body
    assert "10-minute call" in d.body
    assert "[[FOUNDER TO FILL: attach or paste the current price list]]" in d.body
    assert "price_list_placeholder" in d.reason
    assert not lead.email_approved_by_founder


def test_second_outcome_does_not_draft_twice(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", summary="ok")
    wf.on_call_outcome(lead, db, "INTERESTED", summary="again")
    db.commit()
    assert len(_warm_drafts(db, lead)) == 1


def test_maybe_framing_is_backup_not_replacement(db):
    lead = _lead(db, segment="horeca")
    p.record_ai_outcome(lead, db, "INTERESTED", interest="COOL",
                        transcript="We already have a supplier, maybe later as option")
    db.commit()
    d = _warm_drafts(db, lead)[0]
    assert "second instant coffee brand as a backup" in d.body
    for bad in ("replace", "switch", "instead of your", "sample", "100%",
                "paid first order", "trial order", "test call", "margin is up to 30%"):
        assert bad not in d.body.lower()


def test_whatsapp_used_only_when_pacer_eligible(db, monkeypatch):
    from app.services import whatsapp_drafts, whatsapp_pacer
    lead = _lead(db)
    monkeypatch.setattr(whatsapp_pacer, "eligible", lambda db, lead: (True, "ok"))
    r = wf.on_call_outcome(lead, db, "WHATSAPP_OPT_IN", summary="send on WhatsApp")
    assert r["channel"] == "whatsapp" and r["status"] == "PENDING"
    d = whatsapp_drafts.all_drafts()[r["draft_id"]]
    assert d["status"] == "PENDING" and d["template_key"] == "pb_b2b_call_followup"
    assert not d.get("approved_by")
    assert _warm_drafts(db, lead) == []


def test_email_when_not_consented(db, monkeypatch):
    from app.services import whatsapp_pacer
    lead = _lead(db)
    monkeypatch.setattr(whatsapp_pacer, "eligible", lambda db, lead: (False, "no consent"))
    r = wf.on_call_outcome(lead, db, "WHATSAPP_OPT_IN", summary="x")
    assert r["channel"] == "email"


def test_wording_gate_blocks_and_keeps_draft(db, monkeypatch):
    lead = _lead(db)
    monkeypatch.setattr(wf, "compose", lambda *a, **k: {
        "subject": "s", "body": "Shall I send you a free sample jar?", "asked": [], "flags": []})
    r = wf.on_call_outcome(lead, db, "INTERESTED", summary="x")
    assert r["status"] == "BLOCKED_WORDING"


def test_module_never_approves(db, monkeypatch):
    from app.services import send_queue
    called = []
    monkeypatch.setattr(send_queue, "approve", lambda *a, **k: called.append(1))
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", summary="x")
    wf.tick(db, now=datetime.utcnow() + timedelta(days=40))
    assert called == []


# ---------------------------------------------------------------- founder call
def test_founder_call_ask_creates_task_and_no_message(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "CALLBACK_REQUESTED", summary="call me tomorrow 4pm",
                        callback_window="tomorrow 4pm")
    db.commit()
    items = db.query(WorkflowExecution).filter(WorkflowExecution.lead_id == lead.id,
                                               WorkflowExecution.workflow_type == "FOUNDER_CALL",
                                               WorkflowExecution.status == "REQUESTED").all()
    assert len(items) == 1
    assert "WARM_FOUNDER_TASK" in _types(db, lead)
    assert _warm_drafts(db, lead) == []
    from app.services import whatsapp_drafts
    assert whatsapp_drafts.all_drafts() == {}
    assert p.stage_of(lead) == p.FOUNDER_CALL_REQUESTED


# ---------------------------------------------------------------- no / not now
def test_no_stop_is_permanent(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "NOT_INTERESTED", summary="not interested")
    db.commit()
    assert "WARM_FOLLOWUP_SUPPRESSED" in _types(db, lead)
    r = wf.on_call_outcome(lead, db, "INTERESTED", summary="later yes?")
    assert r.get("skipped")
    assert _warm_drafts(db, lead) == []


def test_opt_out_suppresses(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "OPT_OUT", summary="stop calling")
    db.commit()
    assert lead.do_not_call
    assert "WARM_FOLLOWUP_SUPPRESSED" in _types(db, lead)


def test_not_now_parks_then_reopens_after_30_days_as_draft(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "NOT_INTERESTED", transcript="abhi nahi, next month")
    db.commit()
    assert "WARM_FOLLOWUP_PARKED" in _types(db, lead)
    assert wf.tick(db, now=datetime.utcnow() + timedelta(days=29))["reopened"] == []
    r = wf.tick(db, now=datetime.utcnow() + timedelta(days=31))
    assert r["reopened"] == [lead.id]
    ds = _warm_drafts(db, lead)
    assert len(ds) == 1 and ds[0].follow_up_type == "warm_reopen" and ds[0].status == "DRAFT"
    assert wf.tick(db, now=datetime.utcnow() + timedelta(days=95))["reopened"] == []


# ---------------------------------------------------------------- nudges
def test_nudges_at_3_and_7_days_max_two_then_park(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", summary="ok")
    db.commit()
    d = _warm_drafts(db, lead)[0]
    t0 = datetime.utcnow()
    assert wf.tick(db, now=t0 + timedelta(days=5))["nudges"] == []   # not sent yet
    d.status, d.sent_at = "SENT", t0
    db.commit()
    assert wf.tick(db, now=t0 + timedelta(days=2))["nudges"] == []
    assert wf.tick(db, now=t0 + timedelta(days=3, hours=1))["nudges"] == [(lead.id, 1)]
    # nudge 1 still awaiting approval -> nothing stacks
    assert wf.tick(db, now=t0 + timedelta(days=8))["nudges"] == []
    n1 = [x for x in _warm_drafts(db, lead) if x.follow_up_type == "warm_nudge_1"][0]
    assert n1.status == "DRAFT"
    n1.status = "SENT"; db.commit()
    assert wf.tick(db, now=t0 + timedelta(days=8))["nudges"] == [(lead.id, 2)]
    n2 = [x for x in _warm_drafts(db, lead) if x.follow_up_type == "warm_nudge_2"][0]
    n2.status = "SENT"; db.commit()
    assert wf.tick(db, now=t0 + timedelta(days=10))["nudges"] == []
    assert wf.tick(db, now=t0 + timedelta(days=15))["parked"] == [lead.id]
    assert wf.tick(db, now=t0 + timedelta(days=16)) == {"nudges": [], "parked": [], "reopened": []}


def test_reply_stops_nudges(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", summary="ok")
    d = _warm_drafts(db, lead)[0]
    t0 = datetime.utcnow()
    d.status, d.sent_at = "SENT", t0
    db.add(WorkflowEvent(lead_id=lead.id, event_type="EMAIL_REPLY_RECEIVED",
                         occurred_at=t0 + timedelta(days=1)))
    db.commit()
    assert wf.tick(db, now=t0 + timedelta(days=4))["nudges"] == []


def test_kill_switch(db, monkeypatch):
    monkeypatch.setenv("WARM_FOLLOWUP_ENABLED", "0")
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", summary="ok")
    assert _warm_drafts(db, lead) == []


def test_factory_rates_line_in_every_warm_stage(db):
    lead = _lead(db)
    for stage in ("followup", "warm_nudge_1", "warm_nudge_2", "warm_reopen"):
        for cls in (wf.INTERESTED, wf.MAYBE, wf.NOT_NOW):
            m = wf.compose(lead, cls, stage=stage)
            assert wf.FACTORY_LINE in m["body"]
            assert "100%" not in m["body"] and "sample" not in m["body"].lower()
            ok, why = wf.wording_ok(m["subject"], m["body"])
            assert ok, why
