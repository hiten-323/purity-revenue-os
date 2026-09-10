"""
The orchestrator coordinates channels. It owns none of their rules.

A module whose job is to sequence email, WhatsApp and phone is the most
tempting place in this codebase to restate what each of them allows -- and
restating a gate is the bug this system has produced seven times. So the tests
that matter here are the ones asserting it holds no opinion of its own:

  * every gate is imported, not reimplemented
  * a gate that cannot be reached is a NO, never a silent yes
  * it proposes; it never sends
  * a reply stops everything before any channel is considered
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, LeadInteraction
from app.services import outreach_orchestrator as o
from conftest import memory_engine


@pytest.fixture
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from app.services import preference_registry as pref
    f = tmp_path / "dnd.txt"
    f.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None
    return f


def _lead(db, **kw):
    kw.setdefault("company", "Test Cafe")
    kw.setdefault("segment", "horeca")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


# ------------------------------------------------- it restates no rules --

def _executable_source(module) -> str:
    """Source with comments and docstrings removed.

    Splitting on triple quotes does not work: it keeps only the text after the
    LAST docstring in the file, so a check written that way silently inspects
    the tail of the module and passes on anything above it. ast.unparse drops
    comments outright and lets docstrings be stripped explicitly.
    """
    import ast
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_every_gate_is_imported_not_reimplemented():
    body = _executable_source(o)

    assert "trust_promoter import may_send" in body
    assert "whatsapp_sender import consent_check" in body
    assert "founder_call_pipeline import may_place_ai_call" in body

    # the actual vocabularies must appear nowhere in executable code
    for token in ("CONFIDENCE_FLOOR", "MAY_SEND", "IMPLIED_B2B", "OPTED_IN"):
        assert token not in body, (
            f"{token} is restated here; it belongs to the channel that owns it")


def test_an_unreachable_gate_is_a_refusal(db, monkeypatch):
    """A channel whose rule cannot be evaluated has not been satisfied."""
    def boom(lead, d):
        raise RuntimeError("gate module missing")
    monkeypatch.setitem(o.GATES, o.EMAIL, boom)

    result = o.eligibility(_lead(db), db)[o.EMAIL]
    assert result["eligible"] is False
    assert "refusing" in result["reason"]


def test_it_never_sends():
    """Executing a touch belongs to the approval queue and the channel senders,
    where suppression, frequency caps and send-proof live."""
    body = _executable_source(o)
    for forbidden in ("send_email(", "send_whatsapp(", "place_call(", "sendmail("):
        assert forbidden not in body, f"the orchestrator calls {forbidden}"


# --------------------------------------------------------- hard stops --

def test_engagement_stops_the_sequence(db, registry):
    lead = _lead(db, email="a@b.com", phone="9876543210")
    db.add(LeadInteraction(lead_id=lead.id, method="founder_call",
                           outcome="INTERESTED", occurred_at=datetime.utcnow()))
    db.commit()

    assert "engaged" in o.stop_reason(lead, db)
    assert o.next_touch(lead, db)["action"] == "STOP"


def test_no_answer_does_not_stop_the_sequence(db, registry):
    """Nobody reached the buyer. Treating a front desk as engagement would
    silence outreach to every business that has one."""
    lead = _lead(db, phone="9876543210")
    for outcome in ("NO_ANSWER", "BUSY", "GATEKEEPER", "CALL_LATER"):
        db.add(LeadInteraction(lead_id=lead.id, method="founder_call",
                               outcome=outcome, occurred_at=datetime.utcnow()))
    db.commit()

    assert o.stop_reason(lead, db) == ""


def test_do_not_contact_suppresses(db, registry):
    lead = _lead(db, phone="9876543210")
    db.add(LeadInteraction(lead_id=lead.id, method="founder_call",
                           outcome="DO_NOT_CONTACT", occurred_at=datetime.utcnow()))
    db.commit()
    assert "suppressed" in o.stop_reason(lead, db)


def test_dnc_flag_stops_before_any_channel_is_considered(db, registry):
    lead = _lead(db, phone="9876543210", do_not_call=True)
    assert o.next_touch(lead, db)["action"] == "STOP"


# ------------------------------------------------------- eligibility --

def test_linkedin_is_refused_with_a_reason_not_a_silence(db, registry):
    v = o.eligibility(_lead(db), db)[o.LINKEDIN]
    assert v["eligible"] is False
    assert "user agreement" in v["reason"]


def test_whatsapp_needs_a_number_a_verified_account_and_an_opt_in(db, registry):
    """Three separate facts. The middle one used to be assumed: a mobile
    number is not proof of a WhatsApp contact — the network is right and the
    account may simply not exist."""
    lead = _lead(db, whatsapp_number="9876543210")
    assert o.eligibility(lead, db)[o.WHATSAPP]["eligible"] is False, "no opt-in"

    lead.consent_status = "IMPLIED_B2B"
    db.commit()
    v = o.eligibility(lead, db)[o.WHATSAPP]
    assert v["eligible"] is False, "consented, but nobody asked WhatsApp"
    assert "never verified" in v["reason"]

    lead.whatsapp_verified = True
    db.commit()
    assert o.eligibility(lead, db)[o.WHATSAPP]["eligible"] is True


def test_phone_requires_the_dnd_scrub(db, monkeypatch):
    from app.services import preference_registry as pref
    monkeypatch.delenv("DND_SUPPRESSION_FILE", raising=False)
    pref._cache_key = None

    lead = _lead(db, phone="9876543210")
    v = o.eligibility(lead, db)[o.PHONE]
    assert v["eligible"] is False
    assert "preference registry" in v["reason"]


def test_unreachable_lead_reports_every_blocker(db, monkeypatch):
    from app.services import preference_registry as pref
    monkeypatch.delenv("DND_SUPPRESSION_FILE", raising=False)
    pref._cache_key = None

    result = o.next_touch(_lead(db), db)
    assert result["action"] == "UNREACHABLE"
    assert set(result["blocked_by"]) == set(o.CHANNELS)


# ---------------------------------------------------------- planning --

def test_it_proposes_the_first_eligible_channel(db, registry):
    lead = _lead(db, phone="9876543210",
                 stage_entered_date=datetime.utcnow() - timedelta(days=30))
    result = o.next_touch(lead, db)
    assert result["action"] == "PROPOSE"
    assert result["channel"] == o.PHONE      # email/whatsapp are not eligible
    assert "approval" in result["note"]


def test_it_waits_rather_than_jumping_the_schedule(db, registry):
    lead = _lead(db, phone="9876543210", stage_entered_date=datetime.utcnow())
    result = o.next_touch(lead, db)
    assert result["action"] == "WAIT"
    assert result["due_in_days"] > 0


def test_an_ineligible_channel_is_skipped_not_stalled(db, registry):
    """The sequence puts email on day 0. With no address, the programme must
    move to the next eligible channel rather than sit on email forever."""
    lead = _lead(db, phone="9876543210",
                 stage_entered_date=datetime.utcnow() - timedelta(days=30))
    assert o.next_touch(lead, db)["channel"] == o.PHONE


def test_a_used_channel_is_not_repeated(db, registry):
    lead = _lead(db, phone="9876543210",
                 stage_entered_date=datetime.utcnow() - timedelta(days=30))
    lead.ai_call_count = 1
    db.commit()
    result = o.next_touch(lead, db)
    assert result["channel"] != o.PHONE


def test_each_touch_carries_a_distinct_angle():
    """Two emails in one sequence must be different arguments, not the same
    pitch resent."""
    angles = [angle for _, ch, angle in o.SEQUENCE if ch == o.EMAIL]
    assert len(angles) == len(set(angles)), angles


def test_report_counts_rows_not_estimates(db, registry):
    _lead(db, company="A", phone="9876543210")
    _lead(db, company="B")
    r = o.reachability_report(db)
    assert r["leads"] == 2
    assert isinstance(r["by_channel"], dict)
    assert r["reachable_on_at_least_one_channel"] <= r["leads"]
