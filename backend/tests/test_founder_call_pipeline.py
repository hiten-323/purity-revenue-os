"""
The AI qualification call may detect interest. It may not manufacture consent.

The operating model is: one disclosed AI call -> if interested, the founder
calls. That is only safe if three things are true, and each is pinned here:

  1. ONE call. Not "one, then a polite follow-up, then another".
  2. The pipeline's states are not consent values. An AI call attempt must
     never become a WhatsApp or email permission on the next sweep -- which is
     exactly what the removed auto-IMPLIED_B2B block in calling_agent.py did.
  3. The preference-registry scrub fails closed. "We did not check" and "we
     checked and it was clear" must not produce the same outcome.

The consent-leak test is the important one. Every other bug here costs a
wasted dial; that one contacts a business that never agreed to be contacted.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, CallHistory
from app.services import founder_call_pipeline as p
from app.services import preference_registry as pref
from conftest import memory_engine


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A configured registry holding one suppressed number."""
    f = tmp_path / "dnd.txt"
    f.write_text("# operator scrub export\n9000000001\n", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None          # the cache keys on mtime; force a reload
    return f


def _lead(db, **kw):
    lead = B2BLead(company=kw.pop("company", "Test Cafe"),
                   phone=kw.pop("phone", "9876543210"),
                   segment=kw.pop("segment", "horeca"), **kw)
    db.add(lead)
    db.commit()
    return lead


# ------------------------------------------------------- the one-call rule --

def test_one_ai_call_per_lead(db, registry):
    lead = _lead(db)
    assert p.may_place_ai_call(lead)[0] is True

    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()

    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "once per lead" in why
    assert lead.ai_call_count == 1


def test_no_answer_does_not_license_a_retry(db, registry):
    """The tempting bug: treating "no answer" as "not yet attempted"."""
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()
    assert p.stage_of(lead) == p.AI_NO_ANSWER
    assert p.stage_of(lead) in p.TERMINAL
    assert p.may_place_ai_call(lead)[0] is False


# ------------------------------------------------ consent must not be made --

@pytest.mark.parametrize("outcome", sorted(p.OUTCOMES))
def test_no_outcome_ever_writes_consent(db, registry, outcome):
    lead = _lead(db)
    before = lead.consent_status
    p.record_ai_outcome(lead, db, outcome, summary="spoke to the owner")
    db.commit()

    assert lead.consent_status == before
    assert lead.consent_source is None
    assert lead.consent_timestamp is None


def test_interest_is_not_consent(db, registry):
    """The most likely future mistake: "they said yes" -> EXPLICIT."""
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", interest="HOT",
                        summary="wants the founder to call after 4pm",
                        callback_window="after 4pm weekdays")
    p.request_founder_call(lead, db)
    db.commit()

    assert lead.outreach_stage == p.FOUNDER_CALL_REQUESTED
    assert lead.ai_interest_level == "HOT"
    # Interested, queued for the founder -- and still not contactable by
    # WhatsApp or email, because nobody has said they may be.
    assert (lead.consent_status or "UNKNOWN").upper() == "UNKNOWN"


def test_pipeline_stages_are_not_consent_values():
    """If a stage is ever added to CONSENT_OK or CALL_ALLOWED_IF, fail here."""
    p.assert_consent_neutral()


def test_stage_is_invisible_to_the_whatsapp_gate(db, registry):
    from app.services.whatsapp_sender import CONSENT_OK

    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED")
    db.commit()
    assert lead.outreach_stage not in CONSENT_OK


# --------------------------------------------------------- the opt-out path --

def test_opt_out_sets_dnc_but_not_consent(db, registry):
    lead = _lead(db, phone="9111111111")
    p.record_ai_outcome(lead, db, "OPT_OUT", summary="asked not to be called")
    db.commit()

    assert lead.do_not_call is True
    assert "AI call" in (lead.dnc_reason or "")
    assert (lead.consent_status or "UNKNOWN").upper() == "UNKNOWN"
    # and the number is now in the registry, so a later batch cannot redial it
    assert pref.is_suppressed("9111111111")


# ------------------------------------------------------ the registry scrub --

def test_scrub_fails_closed_when_unconfigured(db, monkeypatch):
    monkeypatch.delenv("DND_SUPPRESSION_FILE", raising=False)
    pref._cache_key = None
    lead = _lead(db)

    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "no preference registry configured" in why


def test_suppressed_number_is_refused(db, registry):
    lead = _lead(db, phone="+91 90000 00001")
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "preference registry" in why


def test_empty_registry_is_a_valid_answer(db, tmp_path, monkeypatch):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None

    assert p.may_place_ai_call(_lead(db))[0] is True


# ------------------------------------------------------- state transitions --

def test_illegal_transition_is_refused(db, registry):
    lead = _lead(db)
    with pytest.raises(ValueError, match="not a legal transition"):
        p.advance(lead, db, p.COMMERCIAL_OPPORTUNITY)


def test_terminal_states_are_terminal(db, registry):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "NOT_INTERESTED")
    db.commit()
    with pytest.raises(ValueError, match="terminal"):
        p.advance(lead, db, p.FOUNDER_CALL_REQUESTED)


def test_full_happy_path(db, registry):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", interest="HOT")
    p.request_founder_call(lead, db)
    p.advance(lead, db, p.FOUNDER_CALL_COMPLETED)
    p.advance(lead, db, p.COMMERCIAL_OPPORTUNITY)
    db.commit()

    assert lead.outreach_stage == p.COMMERCIAL_OPPORTUNITY
    assert lead.ai_call_count == 1
    assert (lead.consent_status or "UNKNOWN").upper() == "UNKNOWN"


def test_unknown_outcome_is_refused_not_guessed(db, registry):
    lead = _lead(db)
    with pytest.raises(ValueError, match="never guessed"):
        p.record_ai_outcome(lead, db, "maybe?")
    assert lead.outreach_stage is None


# ------------------------------------------------------------- disclosure --

def test_opening_line_must_disclose_ai():
    assert p.script_discloses(p.OPENING_DISCLOSURE)[0] is True
    assert p.script_discloses("Hi, calling from Pure Pantry about coffee")[0] is False
    assert p.script_discloses("Hi, this is an AI assistant")[0] is False  # no business


# ------------------------------------------------------- the founder brief --

def test_brief_invents_nothing(db, registry):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", interest="WARM",
                        summary="buys from a local roaster, open to samples",
                        callback_window="tomorrow morning")
    db.commit()

    brief = p.founder_brief(lead)
    assert brief["business"] == "Test Cafe"
    assert brief["interest_level"] == "WARM"
    assert brief["requested_callback"] == "tomorrow morning"
    # Never asked, never recorded -- must read as unknown, not as "none".
    assert brief["currently_uses"] is None
    assert brief["objection"] is None


def test_call_is_logged_to_history(db, registry):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "INTERESTED", summary="keen")
    db.commit()

    rows = db.query(CallHistory).filter(CallHistory.lead_id == lead.id).all()
    assert len(rows) == 1
    assert rows[0].status == "AI_INTERESTED"


def test_daily_cap_counts_only_ai_calls(db, registry):
    lead = _lead(db)
    db.add(CallHistory(lead_id=lead.id, status="FOUNDER_CALL"))
    db.commit()
    assert p.calls_placed_today(db) == 0

    p.record_ai_outcome(lead, db, "INTERESTED")
    db.commit()
    assert p.calls_placed_today(db) == 1
    assert p.daily_budget_remaining(db) == p.MAX_AI_CALLS_PER_DAY - 1


# ------------------------------------------- the calling agent routes here --

def test_cold_call_is_routed_through_the_pipeline(db, monkeypatch):
    """An UNKNOWN-consent lead must reach the pipeline gate, not the old path.

    This is the integration that matters: three endpoints call
    trigger_vapi_call, and every lead in the database is UNKNOWN, so if the
    routing is wrong the cold-call rules simply never run.
    """
    from app.services.calling_agent import CallingAgentService

    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "test")
    monkeypatch.delenv("DND_SUPPRESSION_FILE", raising=False)
    pref._cache_key = None

    # check_eligibility gates on `division`, the pipeline on `segment`, and the
    # two columns use different vocabularies (see
    # test_division_and_segment_vocabularies_disagree). Set both so this test
    # measures the routing rather than that mismatch.
    lead = _lead(db, estimated_value=60000.0, segment="distributor",
                 division="distributor")
    lead.consent_status = "UNKNOWN"
    db.commit()

    ok, why = CallingAgentService.trigger_vapi_call(db, lead)
    assert ok is False
    assert "cold_call_refused" in why
    assert "no preference registry configured" in why
    # refused before anything was spent
    assert (lead.ai_call_count or 0) == 0
    assert lead.outreach_stage is None


def test_division_and_segment_vocabularies_disagree(db, registry):
    """A pre-existing defect, pinned so it is visible rather than mysterious.

    check_eligibility rejects on `division`; CALLABLE_SEGMENTS is written in
    the vocabulary of `segment`. The two never agreed, so a cafe whose segment
    is "horeca" is refused as invalid_segment on its division "cafe" -- and
    cafes, restaurants and hotels are the priority queue.

    This test does not assert the behaviour is right. It asserts it is what
    happens, so that changing CALLABLE_SEGMENTS is a decision someone makes on
    purpose rather than a surprise.
    """
    from app.services.calling_agent import CallingAgentService

    lead = _lead(db, segment="horeca", division="cafe", estimated_value=60000.0)
    ok, why = CallingAgentService.check_eligibility(db, lead)
    assert ok is False
    assert why == "invalid_segment"

    # The same business is callable by the pipeline's own reading.
    assert p.may_place_ai_call(lead)[0] is True
