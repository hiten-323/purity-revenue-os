"""
Deterministic dry-run simulator for the purity-coffee-b2b voice agent.

What this can and cannot prove
-------------------------------
The conversation itself is LLM-driven — there is no deterministic way to
prove "given this prospect speech, the model says the right words next"
without either a live model call (non-deterministic, costs money, not what
a dry run means) or a scripted fake LLM (proves the harness, not the model).

What IS deterministic, and what every one of these tests actually pins, is
the half of the system that isn't the model: given that the agent correctly
classified a scenario and called record_call_outcome with the outcome that
scenario should produce, does founder_call_pipeline.record_ai_outcome()
transition to the right stage, touch consent/DNC exactly as it should, and
leave an auditable trail? That is where a real prospect actually gets
protected or not — a model that picks the perfect words but a backend that
mishandles the resulting outcome is the more dangerous bug of the two.

Three of the fifteen requested scenarios (interruption handling, an
unrelated question, and the "is this an AI" question) are conversational
behaviour with no outcome-schema footprint to assert on. Those are covered
by asserting the relevant instruction is actually present in the rendered
system prompt (a "prompt contract" — proves the instruction exists, not
that the model reliably follows it), and otherwise depend on the manual
transcript review in Phase 11. Said plainly in the audit report, not hidden
here.

Provider-call tripwire: nothing in this file imports voice_router,
nuraveda_provider, or anything that could reach a real phone. If a future
edit adds such an import, that is the bug this module exists to catch.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base
from app.services import founder_call_pipeline as pipeline
from conftest import memory_engine

# ── Tripwire ─────────────────────────────────────────────────────────────
_FORBIDDEN_IMPORTS = ("voice_router", "nuraveda_provider", "calling_agent")


def test_tripwire_this_module_cannot_reach_a_real_phone():
    import sys
    this_module = sys.modules[__name__]
    for name in _FORBIDDEN_IMPORTS:
        assert name not in vars(this_module), (
            f"{name} must never be imported into the dry-run simulator — "
            "a scenario test that can dial is not a dry run."
        )


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


def _lead(db, **kw):
    lead = B2BLead(
        company=kw.pop("company", "Test Cafe"),
        phone=kw.pop("phone", "9876543210"),
        segment=kw.pop("segment", "cafe"),
        **kw,
    )
    db.add(lead)
    db.commit()
    return lead


# ── The 15 requested scenarios, mapped to the outcome a correctly-behaving
#    agent should produce, where the scenario has an outcome-schema footprint
#    at all. `None` means: no outcome-handling assertion applies (see the
#    module docstring) — covered by the prompt-contract checks below instead.
SCENARIOS = {
    "1_interested_prospect":            "INTERESTED",
    "2_busy_prospect":                  "CALLBACK_REQUESTED",
    "3_not_interested":                 "NOT_INTERESTED",
    "4_send_details":                   "SEND_INFO_EMAIL",
    "5_callback":                       "CALLBACK_REQUESTED",
    "6_decision_maker_unavailable":     "WRONG_PERSON",
    "7_wrong_number":                   "WRONG_NUMBER",
    "8_dnc":                            "OPT_OUT",
    "9_ai_disclosure_question":         None,   # prompt-contract only
    "10_pricing_objection":             None,   # prompt-contract only (CALL_CONSTRAINTS)
    "11_existing_provider_objection":   "NOT_INTERESTED",
    "12_hindi_prospect":                None,   # language selection, checked separately
    "13_hinglish_prospect":             None,   # same STT/TTS/LLM path as Hindi — no separate code path exists
    "14_prospect_interrupts_agent":     None,   # engine/VAD behaviour, no outcome-schema footprint
    "15_unrelated_question":            None,   # prompt-contract only
}


@pytest.mark.parametrize("scenario,outcome", [(k, v) for k, v in SCENARIOS.items() if v])
def test_scenario_outcome_handling(db, scenario, outcome, tmp_path, monkeypatch):
    """For each scenario with an outcome-schema footprint: simulate the agent
    having correctly classified it (i.e. call record_ai_outcome with the
    outcome that scenario should produce) and verify the pipeline reacts
    correctly — right stage, no manufactured consent, right terminality.

    Isolates DND_SUPPRESSION_FILE for every case, not just OPT_OUT — found
    during this same audit: without this, the OPT_OUT case wrote the
    default test phone straight into the REAL production DND file, because
    record_ai_outcome's do_not_call branch calls preference_registry.add()
    unconditionally, and this test had no env override to catch it.
    """
    dnd_file = tmp_path / "dnd.txt"
    dnd_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(dnd_file))
    import app.services.preference_registry as pref
    pref._cache_key = None

    lead = _lead(db)
    before_consent = lead.consent_status
    before_dnc = lead.do_not_call

    target_stage = pipeline.record_ai_outcome(
        lead, db, outcome, summary=f"dry-run scenario: {scenario}"
    )

    assert pipeline.stage_of(lead) == target_stage
    assert lead.ai_call_count == 1, "one attempt recorded, regardless of outcome"
    assert lead.call_outcome_last == outcome, "granular outcome preserved for reporting"

    # No outcome scenario tested here may manufacture consent — WHATSAPP_OPT_IN
    # is deliberately excluded from SCENARIOS above; it has its own dedicated
    # test below instead of being folded into this generic loop.
    assert lead.consent_status == before_consent
    assert lead.consent_source is None
    assert lead.consent_timestamp is None

    if outcome == "OPT_OUT":
        assert lead.do_not_call is True, "DNC scenario must set do_not_call"
        assert lead.dnc_reason, "DNC scenario must record why"
    else:
        assert lead.do_not_call == before_dnc, "non-DNC scenarios must not touch do_not_call"


def test_scenario_whatsapp_opt_in_creates_consent_with_provenance(db):
    """The one outcome scenario allowed to create consent — verified
    separately from the generic loop above precisely because it is the
    exception, not the rule."""
    lead = _lead(db)
    pipeline.record_ai_outcome(
        lead, db, "WHATSAPP_OPT_IN", summary="asked for the catalogue on WhatsApp"
    )
    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_source == "AI_CALL_WHATSAPP_REQUEST"
    assert lead.consent_timestamp is not None


def test_scenario_send_info_email_is_not_whatsapp_consent(db):
    """The specific bug this scenario exists to catch: 'send me details'
    with no channel named must never be indistinguishable from 'send it on
    WhatsApp' in the outcome data. If this ever collapses to one key again,
    every future 'send details' silently becomes WhatsApp consent."""
    lead = _lead(db)
    pipeline.record_ai_outcome(lead, db, "SEND_INFO_EMAIL", summary="wants details, no channel named")
    assert lead.consent_status != "EXPLICIT"
    assert lead.consent_source is None
    assert lead.call_outcome_last == "SEND_INFO_EMAIL"
    assert lead.call_outcome_last != "WHATSAPP_OPT_IN"


def test_scenario_dnc_terminates_and_registers_suppression(db, tmp_path, monkeypatch):
    """Scenario 8 end to end: the DNC ask must both stop the pipeline
    (terminal stage, may_place_ai_call refuses forever after) and register
    the number so nothing else in the system calls it again either."""
    import app.services.preference_registry as pref

    f = tmp_path / "dnd.txt"
    f.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None

    lead = _lead(db, phone="9123456780")
    pipeline.record_ai_outcome(lead, db, "OPT_OUT", summary="asked to not be called again")

    assert pipeline.stage_of(lead) in pipeline.TERMINAL
    allowed, why = pipeline.may_place_ai_call(lead)
    assert allowed is False
    assert pref.is_suppressed(lead.phone), "opt-out must add the number to the DND registry"


def test_scenario_no_post_dnc_conversation_is_possible():
    """Structural check, not a call trace: OPT_OUT's target stage is
    terminal, so advance() itself refuses any further transition — there is
    no code path back into the conversation from here, not just a
    convention against using one."""
    assert pipeline.AI_OPTED_OUT in pipeline.TERMINAL


# ── Prompt-contract checks for the 4 scenarios with no outcome footprint ──
# Proves the instruction exists in what the model is given. Does not, and
# cannot, prove the model reliably follows it — that is exactly why Phase 11
# requires a real reviewed transcript before any external pilot.

import importlib.util
import sys as _sys
from pathlib import Path

_AGENT_JS = (
    Path(__file__).resolve().parents[3]
    / "ai-voice-agent" / "profiles" / "purity-coffee-b2b" / "agent.js"
)


def _read_agent_js() -> str:
    # ai-voice-agent is a SEPARATE repository, cloned as a sibling directory
    # on a local dev machine. CI checks out only this one repo, so the path
    # never exists there -- this is a real precondition, not something to
    # paper over. skip (not fail) so a CI environment without that sibling
    # checkout doesn't permanently block every PR on this repo; the prompt
    # contract itself is still enforced wherever the sibling IS present
    # (local dev today; a second `actions/checkout` step in ci.yml would
    # extend that to CI, if that's ever wanted).
    if not _AGENT_JS.exists():
        pytest.skip(f"ai-voice-agent not checked out alongside this repo — "
                    f"expected {_AGENT_JS}")
    return _AGENT_JS.read_text(encoding="utf-8")


def test_prompt_contract_ai_disclosure_question():
    src = _read_agent_js()
    assert "Never deny being AI" in src
    assert 'Is this an AI?' in src or "already disclosed" in src


def test_prompt_contract_pricing_objection_stays_with_python_owned_constraints():
    """Pricing behaviour is NOT owned by this prompt — it lives in
    founder_call_pipeline.CALL_CONSTRAINTS and is rendered verbatim
    (constraintsBlock). This test pins that the rendering path still
    exists; the actual no-price rule is pipeline.CALL_CONSTRAINTS's own
    test surface, not duplicated here."""
    src = _read_agent_js()
    assert "constraintsBlock" in src
    assert "HARD RULES" in src
    never_price = [c for c in pipeline.CALL_CONSTRAINTS if "price" in c.lower()]
    assert never_price, "founder_call_pipeline.CALL_CONSTRAINTS must still forbid stating a price"


def test_prompt_contract_unrelated_question_has_no_special_carve_out():
    """There is deliberately no 'if they ask something unrelated' branch —
    the HARD RULES block (Python-owned, unchanged) already forbids
    fabricated claims regardless of what prompts the question, and the
    voice-style rule to keep answers short applies universally rather than
    only to on-script questions.

    2026-09-15 naturalness revision: the exact wording changed from "Keep
    every turn to one or two short sentences" to a more specific cap (short
    acknowledgements are 3-12 words, everything else stops at two sentences)
    as part of making the agent sound less like a script — the invariant
    this test protects (a universal, always-short-answer rule) still holds
    under the new wording.
    """
    src = _read_agent_js()
    assert "Never deliver more than two sentences" in src


def test_prompt_contract_interruption_handling():
    src = _read_agent_js()
    # 2026-09-15 naturalness revision: "stop immediately and listen" became
    # "stop immediately ... don't talk over them, just listen" — split across
    # a fuller sentence, same invariant.
    assert "stop immediately" in src
    assert "just listen" in src
    # Engine-level reinforcement: VAD-based interruption thresholds still
    # configured on the session (see PHASE 6 audit note on minInterruptionWords).
    engine_src = _AGENT_JS.parent.parent.parent.joinpath("src", "livekit-agent.js").read_text(encoding="utf-8")
    assert "minInterruptionWords" in engine_src
    assert "minInterruptionDuration" in engine_src


def test_prompt_contract_language_coverage_hi_en_pa():
    """Scenarios 12 (Hindi) and 13 (Hinglish) share one code path — Sarvam's
    STT/TTS/LLM triad handles code-mixing natively, so there is no separate
    'Hinglish mode'. Punjabi (pa-IN) is asserted here because it is new in
    this revision and has its own welcome fallback + language line, unlike
    Hindi/Hinglish which share the same branch they always did."""
    src = _read_agent_js()
    assert "'pa-IN'" in src
    assert "Speak Punjabi" in src
    engine_src = _AGENT_JS.parent.parent.parent.joinpath("src", "livekit-agent.js").read_text(encoding="utf-8")
    assert "pa-IN" in engine_src


# ── Phase 15 test #10: webhook replay must be idempotent ──────────────────
# Reproduced against the real endpoint FUNCTION (not through FastAPI's HTTP
# layer — this bypasses the require_api_admin dependency, which is fine for
# a unit test of the idempotency logic itself, not a test of auth). Found
# during this same audit: record_ai_outcome()/advance() are deliberately
# strict FSM primitives that reject a stage transitioning to itself, so a
# bare retry of the same outcome used to raise ValueError -> HTTP 400. The
# fix belongs at the webhook boundary, not in the state machine, which is
# exactly where this test lives — importing founder_router, not
# founder_call_pipeline internals.

from app.api.founder_router import post_ai_call_outcome, AICallOutcomeBody  # noqa: E402


def test_outcome_webhook_retry_is_idempotent(db):
    lead = _lead(db, phone="9990002222")
    body = AICallOutcomeBody(lead_id=lead.id, outcome="INTERESTED", summary="first")

    first = post_ai_call_outcome(body, db)
    assert first["status"] == "recorded"

    retry = post_ai_call_outcome(body, db)
    assert retry["status"] == "already_recorded"
    assert retry["stage"] == first["stage"]
    # The retry must not have re-run any of record_ai_outcome's side effects.
    assert lead.ai_call_count == 1


def test_outcome_webhook_actually_commits_to_the_database(db):
    """The bug this exists to catch: post_ai_call_outcome calling
    record_ai_outcome() without ever calling db.commit() itself.
    record_ai_outcome()/advance() never commit (by design — the caller owns
    the transaction, same as every other pipeline caller in this codebase),
    so an endpoint that forgets to commit returns a response that LOOKS
    correct — the stage in the JSON is read from the in-memory object before
    anything is discarded — while persisting nothing at all. Found live via
    a real controlled call whose outcome never appeared in the database
    afterward; reproduced here with db.expire_all(), which forces SQLAlchemy
    to re-SELECT from the database instead of trusting its in-memory cache —
    exactly the gap a same-session assertion (like the retry test above)
    cannot see, because it never stops trusting that cache either."""
    lead = _lead(db, phone="9990004444")
    body = AICallOutcomeBody(lead_id=lead.id, outcome="INTERESTED", summary="durability check")

    result = post_ai_call_outcome(body, db)
    assert result["status"] == "recorded"

    db.expire_all()  # force a real re-SELECT; a mere attribute read would
                      # return the cached (possibly never-persisted) value
    reloaded = db.query(B2BLead).filter(B2BLead.id == lead.id).one()
    assert reloaded.outreach_stage == result["stage"], (
        "response claimed the outcome was recorded, but re-reading from the "
        "database shows it was never committed"
    )
    assert reloaded.call_outcome_last == "INTERESTED"


def test_outcome_webhook_conflicting_outcome_for_same_lead_still_rejected():
    """A DIFFERENT outcome for a lead that already has one recorded is not a
    retry — MAX_AI_COLD_CALLS_PER_LEAD is 1, so there is no legitimate way a
    second real call produced a second result. This must keep failing, not
    silently accept whichever outcome arrives last."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = memory_engine()
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    try:
        lead = _lead(db, phone="9990003333")
        post_ai_call_outcome(
            AICallOutcomeBody(lead_id=lead.id, outcome="INTERESTED", summary="first"), db
        )
        with pytest.raises(Exception):
            post_ai_call_outcome(
                AICallOutcomeBody(lead_id=lead.id, outcome="NOT_INTERESTED", summary="conflict"), db
            )
    finally:
        db.close()
        eng.dispose()
        try:
            os.unlink(path)
        except OSError:
            pass
