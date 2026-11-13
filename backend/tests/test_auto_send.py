"""Auto-send path (29 Oct 2026, Hiten 3:21 PM): explicit, env-gated, never
FOUNDER_APPROVED, every gate before approval and again at send time."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, EmailDraft, WorkflowEvent
from app.services import auto_send as a
from app.services import send_queue as sq
from conftest import memory_engine

GOOD = ("Purity Beans for your store",
        "Hi,\n\nPurica, Purista, Bold and Prima are pure coffee with no chicory. "
        "Ultra Blend is our coffee-chicory blend.\n\nWould a short call help?")


# ------------------------------------------------------------ content gate
@pytest.mark.parametrize("subj,body,verdict", [
    (GOOD[0], GOOD[1], "OK"),
    ("Hi", "Prices: [[FOUNDER TO FILL: price list]]", "HOLD"),
    ("Hi", "See [[ attach ]] here", "HOLD"),
    ("Hi", "Shall we send a sample?", "BLOCK"),
    ("Hi", "Try our samples today.", "BLOCK"),
    ("Hi", "A tasting is arranged.", "BLOCK"),
    ("Hi", "Our coffee is 100% pure.", "BLOCK"),
    ("Hi", "Bold is freeze-dried.", "BLOCK"),
    ("Hi", "Ultra Blend is freeze dried.", "BLOCK"),
    ("Hi", "Ultra Blend is pure coffee.", "BLOCK"),
    ("Hi", "Ultra Blend has no chicory.", "BLOCK"),
    ("Hi", "Ultra Blend is a great everyday coffee.", "BLOCK"),
    ("Hi", "The Variety Box is pure coffee, no chicory.", "BLOCK"),
    ("Hi", "Start with a small paid first order.", "BLOCK"),
    ("Hi", "There is no MOQ.", "BLOCK"),
    ("Hi", "Keep us instead of your current brand: replace your supplier.", "BLOCK"),
    ("Hi", "Bold and Ultra Blend are agglomerated. Ultra Blend contains chicory.", "OK"),
])
def test_content_check(subj, body, verdict):
    v, why = a.content_check(subj, body)
    assert v == verdict, why


def test_window():
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    assert a.in_window(datetime(2026, 10, 29, 10, 0, tzinfo=ist))        # Thu
    assert not a.in_window(datetime(2026, 10, 29, 9, 29, tzinfo=ist))
    assert not a.in_window(datetime(2026, 10, 29, 19, 0, tzinfo=ist))
    assert not a.in_window(datetime(2026, 11, 1, 11, 0, tzinfo=ist))      # Sunday


def test_default_off(monkeypatch):
    monkeypatch.delenv("OUTREACH_AUTO_SEND", raising=False)
    assert not a.enabled()


def test_hold_ids(monkeypatch):
    monkeypatch.setenv("AUTO_SEND_HOLD_LEAD_IDS", "7, 9")
    monkeypatch.setenv("EMAIL_HOLD_LEAD_IDS", "11")
    assert {7, 9, 11, 1245} <= a.hold_ids()


# ------------------------------------------------------------ approvals
def test_auto_actor_counts_only_when_on(monkeypatch):
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "0")
    assert not sq.is_approval_actor("AUTO_SEND")
    assert sq.is_approval_actor("FOUNDER")
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    assert sq.is_approval_actor("AUTO_SEND")
    assert not sq.is_approval_actor("AUTO_WORKER")
    assert not sq.is_founder_actor("AUTO_SEND")          # never impersonates the founder
    class L: id = 1; email = "x@example.com"
    with pytest.raises(PermissionError):
        sq.approve(L(), None, approved_by="AUTO_WORKER")


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app.services import whatsapp_drafts
    monkeypatch.setattr(whatsapp_drafts, "STORE", tmp_path / "wa.json")
    monkeypatch.setenv("EMAIL_DAILY_STATE_FILE", str(tmp_path / "daily.json"))
    monkeypatch.setenv("EMAIL_DAILY_CAP", "15")
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


def _lead_draft(db, body=GOOD[1], **kw):
    _em = kw.pop("email", "owner@sharma.example")
    kw.setdefault("email_trust", "TRUSTED")
    kw.setdefault("email_confidence", 80)
    kw.setdefault("email_source", "WEBSITE")
    kw.setdefault("email_mx_valid", 1)
    kw.setdefault("email_verified", 1)
    kw.setdefault("email_verification_status", "VALID")
    l = B2BLead(company=kw.pop("company", "Store " + (_em or "noemail")), email=_em,
                phone="9855593323", **kw)
    db.add(l); db.commit()
    d = EmailDraft(lead_id=l.id, subject=GOOD[0], body=body, status="DRAFT", recipient=l.email)
    db.add(d); db.commit()
    return l, d


def _pass_lead_check(monkeypatch):
    monkeypatch.setattr(a, "lead_check", lambda db, lead, recipient="", decision=True: (True, "ok"))
    monkeypatch.setattr(a, "in_window", lambda now=None: True)


def test_evaluate_approves_marks_auto_not_founder(db, monkeypatch):
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    _pass_lead_check(monkeypatch)
    l, d = _lead_draft(db)
    rep = a.evaluate_email(db, dry_run=False)
    db.refresh(d)
    assert rep["approved"] == [d.id]
    assert d.status == "AUTO_APPROVED" and d.status != "FOUNDER_APPROVED"
    assert "auto (Hiten 29 Oct 3:21 PM)" in d.reason
    ev = db.query(WorkflowEvent).filter(WorkflowEvent.event_type == "OUTREACH_APPROVED").one()
    assert ev.actor == "AUTO_SEND" and ev.payload["approved_by"] == "auto (Hiten 29 Oct 3:21 PM)"
    assert [x.id for x in sq.pending(db)] == [l.id]
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "0")          # rollback = flag flip
    assert sq.pending(db) == []


def test_dry_run_and_flag_off_change_nothing(db, monkeypatch):
    _pass_lead_check(monkeypatch)
    l, d = _lead_draft(db)
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    rep = a.evaluate_email(db, dry_run=True)
    assert rep["would_approve"] == [d.id] and rep["approved"] == []
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "0")
    rep = a.evaluate_email(db, dry_run=False)
    db.refresh(d)
    assert d.status == "DRAFT" and rep["approved"] == []
    assert db.query(WorkflowEvent).count() == 0


def test_markers_and_bad_copy_never_approved(db, monkeypatch):
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    _pass_lead_check(monkeypatch)
    _, d1 = _lead_draft(db, body="Price: [[FOUNDER TO FILL: price list]]")
    _, d2 = _lead_draft(db, body="Start with a small paid first order.", email="b@other.example")
    rep = a.evaluate_email(db, dry_run=False)
    db.refresh(d1); db.refresh(d2)
    assert d1.status == "DRAFT" and d2.status == "DRAFT"
    assert rep["held_markers"] == [d1.id] and rep["blocked_content"][0][0] == d2.id


def test_blocked_statuses_never_read(db, monkeypatch):
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    _pass_lead_check(monkeypatch)
    for st in ("BLOCKED_AUTO_APPROVAL", "BLOCKED_NOT_SENDABLE", "BLOCKED_LOW_TRUST"):
        _, d = _lead_draft(db, email=f"{st.lower()}@x{st[-3:].lower()}.example")
        d.status = st; db.commit()
    rep = a.evaluate_email(db, dry_run=False)
    assert rep["would_approve"] == [] and rep["approved"] == []
    assert {d.status for d in db.query(EmailDraft)} == {"BLOCKED_AUTO_APPROVAL", "BLOCKED_NOT_SENDABLE", "BLOCKED_LOW_TRUST"}


def test_room_respects_daily_cap(db, monkeypatch):
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    monkeypatch.setenv("EMAIL_DAILY_CAP", "2")
    _pass_lead_check(monkeypatch)
    for i in range(4):
        _lead_draft(db, email=f"o{i}@shop{i}.example")
    rep = a.evaluate_email(db, dry_run=False)
    assert len(rep["approved"]) == 2 and len(rep["would_approve"]) == 4
    assert a.room(db) == 0


def test_outside_window_no_approval(db, monkeypatch):
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    monkeypatch.setattr(a, "lead_check", lambda db, lead, recipient="", decision=True: (True, "ok"))
    monkeypatch.setattr(a, "in_window", lambda now=None: False)
    _lead_draft(db)
    assert a.evaluate_email(db, dry_run=False)["approved"] == []


# ------------------------------------------------------------ lead gate
@pytest.mark.parametrize("kw,why", [
    ({"do_not_call": True}, "do_not_call"),
    ({"contact_status": "OPTED_OUT"}, "contact_status"),
    ({"email": ""}, "no email"),
])
def test_lead_check_blocks(db, kw, why):
    l, _ = _lead_draft(db, **kw)
    ok, reason = a.lead_check(db, l, decision=False)
    assert not ok and why in reason


def test_lead_1245_always_held(db):
    l = B2BLead(id=1245, company="Jain Trading Co", email="j@jain.example"); db.add(l); db.commit()
    ok, why = a.lead_check(db, l, decision=False)
    assert not ok and "hold" in why


def test_recipient_mismatch_blocked(db):
    l, _ = _lead_draft(db)
    ok, why = a.lead_check(db, l, recipient="someone@else.example", decision=False)
    assert not ok and "recipient" in why


def test_email_auto_approval_requires_separate_address_bound_consent(db):
    l, _ = _lead_draft(db)
    ok, why = a.lead_check(db, l, decision=False)
    assert not ok
    assert "email channel authorization" in why


def test_send_time_check_off_when_flag_off(db, monkeypatch):
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "0")
    l, d = _lead_draft(db)
    assert a.send_time_check(db, l, d) == (False, "OUTREACH_AUTO_SEND is off")


# ------------------------------------------------------------ whatsapp
def test_wa_auto_approve_first_message_only(db, monkeypatch):
    from app.services import whatsapp_drafts as wd, whatsapp_pacer
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    monkeypatch.setattr(whatsapp_pacer, "eligible", lambda db, lead: (True, "eligible"))
    l, _ = _lead_draft(db)
    first = wd.create(l.id, "pb_b2b_call_followup", {"followup_type": "warm_followup", "flags": []})
    l2, _ = _lead_draft(db, email="n@n2.example")
    nudge = wd.create(l2.id, "pb_b2b_call_followup", {"followup_type": "warm_nudge_1"})
    l3, _ = _lead_draft(db, email="f@f3.example")
    flagged = wd.create(l3.id, "pb_b2b_call_followup", {"followup_type": "warm_followup", "flags": ["moq_placeholder"]})
    rep = a.evaluate_whatsapp(db, dry_run=False)
    allv = wd.all_drafts()
    assert rep["approved"] == [first]
    assert allv[first]["status"] == "APPROVED" and allv[first]["template_key"] == "pb_b2b_fullrange_hinglish"
    assert allv[first]["approved_by"] == "auto (Hiten 29 Oct 3:21 PM)"
    assert allv[nudge]["status"] == "PENDING" and allv[flagged]["status"] == "PENDING"


def test_wa_hold_and_flag_off(db, monkeypatch):
    from app.services import whatsapp_drafts as wd, whatsapp_pacer
    monkeypatch.setattr(whatsapp_pacer, "eligible", lambda db, lead: (True, "eligible"))
    l, _ = _lead_draft(db)
    did = wd.create(l.id, "pb_b2b_fullrange_hinglish", {})
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "0")
    assert a.evaluate_whatsapp(db, dry_run=False)["approved"] == []
    with pytest.raises(PermissionError):
        wd.auto_approve(did, "x")
    monkeypatch.setenv("OUTREACH_AUTO_SEND", "1")
    monkeypatch.setenv("AUTO_SEND_HOLD_LEAD_IDS", str(l.id))
    assert a.evaluate_whatsapp(db, dry_run=False)["approved"] == []
    assert wd.all_drafts()[did]["status"] == "PENDING"


def test_worker_never_writes_founder_approved():
    import pathlib
    src = pathlib.Path(a.__file__).read_text()
    assert '"FOUNDER_APPROVED"' in src            # only as a status it refuses to duplicate
    assert 'status = "FOUNDER_APPROVED"' not in src and "FOUNDER" != a.AUTO_ACTOR


# ------------------------------------------------------------ template fix (29 Oct 4:08 PM)
def test_fixed_templates_pass_every_gate():
    import inspect
    from app.services import outreach_engine as oe, email_sender as es, wording_gate as wg
    from app.services.outreach_engine import SKUS
    lines = [SKUS[k].line(p) for k in SKUS for p in ("both", "50", "100")]
    assert "chicory" in SKUS["ultra_blend"].line() and "agglomerated" in SKUS["ultra_blend"].line()
    assert "agglomerated" in SKUS["bold"].line()
    src = inspect.getsource(oe) + inspect.getsource(es)
    assert "freeze-dried and agglomerated" not in src
    assert "Ultra Blend (signature" not in src and '"signature"' not in src
    body = "\n".join(["Hi,", "Our product range:"] + ["• " + l for l in lines] + [
        "Purity Beans is a premium instant coffee brand of Pure Pantry Provisions, with zero artificial additives. "
        "Purica and Purista are freeze-dried. Bold and Ultra Blend are agglomerated. "
        "Purica, Purista, Bold and Prima are pure coffee with no chicory. "
        "Ultra Blend is our coffee-chicory blend (it contains chicory)."])
    assert wg.check("Purity Beans", body)[0]
    assert a.content_check("Purity Beans", body) == ("OK", "")
