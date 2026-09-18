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
from app.services import identity
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
    f.write_text("# operator scrub export\n9845123067\n", encoding="utf-8")
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

def test_request_founder_call_creates_actionable_idempotent_work_item(db):
    lead = _lead(db)
    p.record_ai_outcome(lead, db, "HUMAN_HANDOFF", summary="Interested; wants founder to call")
    p.request_founder_call(lead, db, note="Human handoff requested")
    from app.models.models import WorkflowExecution, WorkflowEvent
    items = db.query(WorkflowExecution).filter(
        WorkflowExecution.lead_id == lead.id,
        WorkflowExecution.workflow_type == "FOUNDER_CALL",
        WorkflowExecution.status == "REQUESTED",
    ).all()
    assert len(items) == 1
    assert items[0].requested_by == "AI"
    assert items[0].payload["founder_brief"]["business"] == lead.company
    events = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type == "FOUNDER_CALL_REQUESTED",
    ).all()
    assert len(events) == 1


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

@pytest.mark.parametrize(
    "outcome", sorted(set(p.OUTCOMES) - {"WHATSAPP_OPT_IN"}))
def test_no_outcome_writes_consent_except_an_explicit_request(db, registry, outcome):
    """The invariant is unchanged: hearing interest is not being given
    permission. One outcome is excluded, and only one — WHATSAPP_OPT_IN, where
    the business itself asked to be messaged. That exception is narrow, it is
    tested by name below, and it is the ONLY way consent enters this system
    from a call.

    Widening this exclusion set is how "they sounded keen" becomes a licence.
    """
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
    """If a stage is ever added to CONSENT_OK, fail here."""
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


def test_fabricated_placeholder_phone_is_refused(db, registry):
    lead = _lead(db, phone="+91 88888 88888")
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "fabricated" in why


def test_id_derived_phone_is_refused(db, registry):
    """The exact 2026-07-17 incident: a phone ending in this lead's own row
    id, zero-padded to 5 digits — a stale seed value, not a real number."""
    lead = _lead(db, phone="+91 98765 00000")  # placeholder; id set below
    lead.phone = f"+91 98765 {str(lead.id).zfill(5)}"
    db.commit()
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "derived from this lead's own id" in why


def test_phone_shared_by_many_unrelated_leads_is_refused(db, registry):
    """The 2026-09-15 incident: a still-unroot-caused process put the same
    well-formed, non-fabricated-looking number on 150+ unrelated leads. This
    is the containment — a number that answers for dozens of different
    businesses is not this lead's phone, whatever its digits look like."""
    shared = "+91 73260 59369"
    for i in range(identity.SHARED_PHONE_THRESHOLD):
        _lead(db, company=f"Other Business {i}", phone=shared)
    target = _lead(db, company="The Actual Lead", phone=shared)

    ok, why = p.may_place_ai_call(target)
    assert ok is False
    assert "other leads" in why


def test_a_number_shared_by_only_a_few_leads_is_not_refused(db, registry):
    """The threshold exists so a genuinely shared line (a small chain's
    single reception desk) doesn't get refused on principle alone."""
    shared = "+91 88990 11223"
    for i in range(identity.SHARED_PHONE_THRESHOLD - 2):
        _lead(db, company=f"Branch {i}", phone=shared)
    target = _lead(db, company="Branch Main", phone=shared)

    ok, why = p.may_place_ai_call(target)
    assert ok is True


def test_a_real_looking_phone_is_not_flagged_as_fabricated(db, registry):
    """Guard against the fabrication checks being too aggressive — a normal
    number must not collide with either pattern."""
    lead = _lead(db, phone="+91 98765 43210")
    ok, why = p.may_place_ai_call(lead)
    assert ok is True


def test_suppressed_number_is_refused(db, registry):
    lead = _lead(db, phone="+91 98451 23067")
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


def test_division_and_segment_vocabularies_now_agree(db, registry):
    """This test used to assert the opposite, and that was the point of it.

    check_eligibility gates `division`; the pipeline gates `segment`; the two
    columns use different vocabularies. A cafe whose segment is "horeca" was
    refused as invalid_segment on its division "cafe" — and cafes are the first
    entry on the priority list.

    The original version pinned that as a fact rather than a decision, so that
    widening the list would be a deliberate act. It was widened deliberately on
    2026-09-10 (founder: all categories callable), so this now guards the fix:
    the same business must not pass one gate and fail the other.
    """
    from app.services.calling_agent import CallingAgentService

    lead = _lead(db, segment="horeca", division="cafe", estimated_value=60000.0)

    ok, why = CallingAgentService.check_eligibility(db, lead)
    assert why != "invalid_segment", (
        "the division gate still refuses a category the segment gate allows")

    # Callable by both readings now.
    assert p.may_place_ai_call(lead)[0] is True


# ------------------------------------------------- one callable-category set --

def test_the_two_gates_share_one_category_set():
    """They did not, and it cost 618 businesses.

    CallingAgentService.CALLABLE_SEGMENTS gated `lead.division`; the pipeline's
    gated `lead.segment`; the two columns use different vocabularies. The same
    business could pass one gate and fail the other, and nobody had decided
    that — the lists simply held near-synonyms that never matched.
    """
    from app.services.calling_agent import CallingAgentService

    assert set(CallingAgentService.CALLABLE_SEGMENTS) == set(p.CALLABLE_SEGMENTS)


def test_both_vocabularies_are_covered():
    """Whichever column a caller reads, the answer must be the same."""
    segment_values = {"corporate", "grocery", "distributor", "horeca",
                      "corporate_office", "cafe", "wholesaler",
                      "facility_management", "hospital"}
    division_values = {"restaurant", "retail_kirana", "supermarket", "hotel",
                       "distributor", "corporate_office", "cafe",
                       "manufacturing", "modern_trade", "wholesaler",
                       "facility_management", "hospital", "school", "college",
                       "office_pantry", "institutional_buyer", "unknown"}

    missing = (segment_values | division_values) - set(p.CALLABLE_CATEGORIES)
    assert not missing, (
        f"these categories exist in the data but are not callable: "
        f"{sorted(missing)}. A category that appears in b2b_leads and not here "
        f"is silently uncallable — which is exactly how cafes ended up refused.")


def test_the_priority_segments_are_callable():
    """Cafe, hotel and restaurant are the top of the stated priority list and
    were all refused by the old five-name list."""
    for named in ("cafe", "hotel", "restaurant", "horeca", "grocery",
                  "retail_kirana", "supermarket", "corporate_office"):
        assert named in p.CALLABLE_CATEGORIES, named


def test_a_blank_category_is_still_refused(db, registry):
    """"unknown" is a recorded category — we looked and could not tell. Blank
    is an incomplete record, which is a different thing."""
    lead = _lead(db, segment="")
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "not callable" in why

    lead.segment = "unknown"
    db.commit()
    assert p.may_place_ai_call(lead)[0] is True


# ------------------------------------- the one outcome that creates consent --

def test_whatsapp_opt_in_is_the_only_thing_that_grants_whatsapp(db, registry):
    """A business asking us to WhatsApp them IS a Meta-compliant opt-in.

    This is the only place in the system where consent is created rather than
    read, and it is created the way Meta requires: the business asked, on a
    recorded call, in its own words.
    """
    lead = _lead(db)
    assert (lead.consent_status or "UNKNOWN").upper() == "UNKNOWN"

    p.record_ai_outcome(lead, db, "WHATSAPP_OPT_IN",
                        summary="asked us to send the catalogue on WhatsApp")
    db.commit()

    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_source == "AI_CALL_WHATSAPP_REQUEST"
    assert lead.consent_timestamp is not None
    # and they are interested, so the founder queue gets them too
    assert lead.outreach_stage == p.AI_INTEREST_DETECTED


@pytest.mark.parametrize("outcome", ["INTERESTED", "NOT_INTERESTED",
                                     "NO_ANSWER", "WRONG_NUMBER", "OPT_OUT"])
def test_no_other_outcome_grants_whatsapp(db, registry, outcome):
    """Interest is not permission. Only an explicit request is."""
    lead = _lead(db)
    p.record_ai_outcome(lead, db, outcome)
    db.commit()
    assert (lead.consent_status or "UNKNOWN").upper() in ("UNKNOWN", "")


def test_whatsapp_opt_in_binds_consent_to_the_number_actually_called(db, registry):
    """Consent is granted for a NUMBER, not for the lead row in general.

    Without this, a later change to phone/whatsapp_number (re-enrichment, a
    manual correction, or the 2026-09-15 class of corruption bug that put one
    fabricated number on 1,166 leads) would silently carry an old opt-in over
    to a destination that never gave it. consent_phone is what
    whatsapp_sender.consent_check() checks the current number against.
    """
    lead = _lead(db, phone="9876500001")
    p.record_ai_outcome(lead, db, "WHATSAPP_OPT_IN", summary="send it on WhatsApp")
    db.commit()

    assert lead.consent_phone == "9876500001"


def test_whatsapp_opt_in_prefers_whatsapp_number_over_phone_for_binding(db, registry):
    """Matches send_whatsapp()'s own destination resolution order, so the
    number consent is checked against at send time is the number it was
    captured against at grant time."""
    lead = _lead(db, phone="9876500001", whatsapp_number="9111100002")
    p.record_ai_outcome(lead, db, "WHATSAPP_OPT_IN", summary="send it on WhatsApp")
    db.commit()

    assert lead.consent_phone == "9111100002"


def test_verification_alone_never_grants_whatsapp(db, registry):
    """The link this deliberately does NOT make.

    whatsapp_verified proves an account exists on a number. It is a technical
    fact. Treating it as permission would be turning a fact into a licence to
    contact someone — the defect this codebase keeps producing.
    """
    from app.services import outreach_orchestrator as o

    lead = _lead(db)
    lead.whatsapp_number = "9876543210"
    lead.whatsapp_verified = True          # WhatsApp says the account exists
    db.commit()

    v = o.eligibility(lead, db)["whatsapp"]
    assert v["eligible"] is False, "verification was treated as consent"
    assert "opt-in" in v["reason"]

    # Now the business actually asks. Only now.
    p.record_ai_outcome(lead, db, "WHATSAPP_OPT_IN", summary="send it on WhatsApp")
    db.commit()
    assert o.eligibility(lead, db)["whatsapp"]["eligible"] is True


def test_the_call_carries_constraints_not_just_questions():
    """Asked "what is your price?" with no constraints, the live model replied
    "Our wholesale price ... is Rs 450 per kilogram." Nobody gave it a price.

    The profile's _scope_note says the AI never negotiates price, but that is a
    note for humans. This asserts the rule actually reaches the model.
    """
    from app.services import founder_call_pipeline as p

    assert p.CALL_CONSTRAINTS, "no constraints defined"
    blob = " ".join(p.CALL_CONSTRAINTS).lower()
    assert "never state a price" in blob
    assert "never take an order" in blob
    assert "do not guess" in blob

    # The forbidden-claims list must match the one the email copy honours.
    for banned in ("turnover", "iso", "capacity", "government supply", "client"):
        assert banned in blob, f"{banned} is not forbidden to the caller"


def test_constraints_are_dispatched_with_every_qualification_call():
    """A rule that exists but is never sent is decoration."""
    import ast
    import inspect
    import textwrap

    from app.services.calling_agent import CallingAgentService

    # dedent, not lstrip: lstrip only fixes the first line, leaving the body
    # indented and ast.parse raising IndentationError.
    src = textwrap.dedent(inspect.getsource(CallingAgentService._place_qualification_call))
    tree = ast.parse(src)
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert "constraints" in keys, (
        "the qualification call ships opening and questions but not the "
        "constraints — the model will invent prices")
    assert "opening" in keys and "questions" in keys
