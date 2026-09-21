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


@pytest.fixture
def voice_ready(monkeypatch):
    """A configured, armed voice provider.

    The sequencing tests below are about ORDER — propose every eligible
    day-0 channel, skip an ineligible one. They always assumed phone was
    usable; that assumption was implicit until the gate started checking
    provider capability and the arming flag as well as permission. Making
    it explicit is not a relaxation: the no-provider and flag-off cases
    have their own tests.
    """
    from app.services import voice_router
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setattr(voice_router, "config_status",
                        lambda: (True, "nuraveda ready (test fixture)"))


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


def test_whatsapp_is_isolated_unless_aisensy_is_on(db, registry, monkeypatch):
    """WhatsApp stays off. Isolation is the default, not a missing opt-in."""
    monkeypatch.delenv("AISENSY_ENABLED", raising=False)
    lead = _lead(db, whatsapp_number="9876543210", consent_status="EXPLICIT")
    v = o.eligibility(lead, db)[o.WHATSAPP]
    assert v["eligible"] is False
    assert "AISENSY_ENABLED" in v["reason"]


def test_whatsapp_needs_a_number_and_an_opt_in(db, registry, monkeypatch):
    """Two facts now, not three — and only after the isolation switch is on.

    Verification used to be the middle one, on the true principle that a
    mobile number is not proof of a WhatsApp contact. It is no longer asked
    because it can no longer be answered: whatsapp_verified came from
    Evolution's /chat/whatsappNumbers, a WhatsApp-Web capability, and Meta's
    official platform exposes no equivalent by design.

    So NULL is permissive and False is not — the distinction this codebase
    stated from the start and now depends on.
    """
    monkeypatch.setenv("AISENSY_ENABLED", "1")
    lead = _lead(db, whatsapp_number="9876543210")
    v = o.eligibility(lead, db)[o.WHATSAPP]
    assert v["eligible"] is False, "no opt-in"
    assert "opt-in" in v["reason"]

    lead.consent_status = "EXPLICIT"
    db.commit()
    assert lead.whatsapp_verified is None
    assert o.eligibility(lead, db)[o.WHATSAPP]["eligible"] is True, (
        "NULL means unasked and unaskable, not refused")

    lead.whatsapp_verified = False           # asked once, and WhatsApp said no
    db.commit()
    v = o.eligibility(lead, db)[o.WHATSAPP]
    assert v["eligible"] is False
    assert "no WhatsApp account" in v["reason"]

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

def test_it_proposes_the_first_eligible_channel(db, registry, voice_ready):
    lead = _lead(db, phone="9876543210",
                 stage_entered_date=datetime.utcnow() - timedelta(days=30))
    result = o.next_touch(lead, db)
    assert result["action"] == "PROPOSE"
    assert result["channel"] == o.PHONE      # email/whatsapp are not eligible
    assert "approval" in result["note"]


def test_day0_phone_is_not_held_for_a_calendar_gap(db, registry, voice_ready):
    """Phone used to sit on day 4. A same-day eligible call must go out with
    email, not wait for a sequence slot that no longer exists."""
    lead = _lead(db, phone="9876543210", stage_entered_date=datetime.utcnow())
    result = o.plan_channels(lead, db)
    assert result["action"] == "PROPOSE"
    assert result["kind"] == "CALL"
    assert o.PHONE in result["channels"]


def test_an_ineligible_channel_is_skipped_not_stalled(db, registry, voice_ready):
    """Day 0 email with no address must not stall the phone call."""
    lead = _lead(db, phone="9876543210",
                 stage_entered_date=datetime.utcnow() - timedelta(days=30))
    assert o.next_touch(lead, db)["channel"] == o.PHONE


def test_a_used_channel_is_not_repeated(db, registry, voice_ready):
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


def test_phone_needs_a_provider_not_just_permission(db, registry, monkeypatch):
    """Permission and capability are different questions.

    A dry run over 150 candidates proposed phone for 145 of them: email and
    WhatsApp were ineligible, so the sequence fell through to phone, and
    may_place_ai_call happily allowed every one. None could be placed -- no
    voice provider is configured. The founder queue would have filled with
    work nobody could do, and the system would have reported readiness it did
    not have.
    """
    from app.services import voice_router

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    lead = _lead(db, phone="9876543210")

    monkeypatch.setattr(voice_router, "config_status",
                        lambda: (False, "nuraveda: NURAVEDA_ENABLED is not set to 1"))
    v = o.eligibility(lead, db)[o.PHONE]
    assert v["eligible"] is False
    assert "no voice provider can place it" in v["reason"]
    assert "NURAVEDA_ENABLED" in v["reason"], "the reason must name what to fix"

    monkeypatch.setattr(voice_router, "config_status",
                        lambda: (True, "nuraveda ready"))
    assert o.eligibility(lead, db)[o.PHONE]["eligible"] is True


def test_email_draft_only_does_not_suppress_an_eligible_call(db, registry, voice_ready):
    """The DRAFT_ONLY bug: email trust failure is not a human-conversation
    exception, and it must not hide a callable number.

    DISCOVERED is MAY_DRAFT_ONLY — evaluate_next_action returns DRAFT_ONLY,
    which used to be readable as 'do not contact this lead'. Phone eligibility
    is a different question and must still propose the call.
    """
    from app.services.trust_promoter import DISCOVERED
    from app.services.decision_engine import evaluate_next_action

    lead = _lead(db, phone="9876543210", email="info@untrusted.example",
                 email_trust=DISCOVERED, email_confidence=20,
                 estimated_value=0,
                 stage_entered_date=datetime.utcnow())
    email_verdict = evaluate_next_action(lead, db)
    assert email_verdict["action"] != "SEND", email_verdict


    plan = o.plan_channels(lead, db)
    assert plan["action"] == "PROPOSE"
    assert o.PHONE in plan["channels"]
    assert o.EMAIL not in plan["channels"]
    assert plan["kind"] == "CALL"


def test_eligible_email_and_phone_are_both_proposed(db, registry, voice_ready):
    from app.services.trust_promoter import VERIFIED

    lead = _lead(db, phone="9876543210", email="owner@cafe.example",
                 email_trust=VERIFIED, email_confidence=80,
                 stage_entered_date=datetime.utcnow())
    plan = o.plan_channels(lead, db)
    assert plan["action"] == "PROPOSE"
    assert plan["kind"] == "BOTH"
    assert set(plan["channels"]) == {o.EMAIL, o.PHONE}
    assert plan["channel"] == "both"


def test_calling_flag_off_blocks_phone_even_when_permitted(db, registry, monkeypatch):
    from app.services import voice_router
    monkeypatch.setenv("AI_CALLING_ENABLED", "0")
    monkeypatch.setattr(voice_router, "config_status",
                        lambda: (True, "nuraveda ready"))
    lead = _lead(db, phone="9876543210")
    v = o.eligibility(lead, db)[o.PHONE]
    assert v["eligible"] is False
    assert "AI_CALLING_ENABLED" in v["reason"]


def test_an_unavailable_voice_router_is_a_refusal(db, registry, monkeypatch):
    """A channel whose provider readiness cannot even be established has not
    been established. Fail closed, exactly as the email and WhatsApp gates do."""
    from app.services import voice_router

    def _boom():
        raise RuntimeError("voice_router is broken")

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setattr(voice_router, "config_status", _boom)
    v = o.eligibility(_lead(db, phone="9876543210"), db)[o.PHONE]
    assert v["eligible"] is False
    assert "unavailable" in v["reason"]
    assert "RuntimeError" in v["reason"], "name what broke, so it is actionable"
