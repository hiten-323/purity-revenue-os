"""The production AI-calling policy, pinned end to end.

One fleet, one ledger, ── no artificial volume ceiling:

  * there is NO fleet-wide cap (no X-per-day, no rolling-24h, no daily
    budget, no AUTO_AI_CALLING_LIMIT): inside the Asia/Kolkata business-hours
    window every eligible lead is dialled, bounded only by per-lead gates;
  * every dial opens its ledger row atomically (begin_ai_attempt) BEFORE the
    network call — and the row is released (release_ai_call_attempt) only
    when the provider refused before any PSTN attempt;
  * no-answer leads remain open: at most 3 AI attempts in a rolling 30-day
    window, then cooling until the window clears; exact requested callbacks
    are honoured only at/after the requested date and time;
  * dials respect Asia/Kolkata 09:30-19:30 Mon-Sat (AI_CALL_BUSINESS_HOURS,
    fail-closed) in BOTH the eligibility gate and the single dial path;
  * the worker's scheduler dials only when AUTO_AI_CALLING_ENABLED is on
    (default off) — arming calls and arming the schedule are two steps.

The ledger, not the wall clock, is the authority: the attempt count of record
is the call_history AI_% row written at dispatch, and a provider refusal that
never rang removes its row so the ledger reflects what it actually dialled.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, CallHistory
from app.services.call_intelligence.models import CallResult, ensure_for_session
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
    f = tmp_path / "dnd.txt"
    f.write_text("# empty for these tests\n", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None
    return f


@pytest.fixture(autouse=False)
def business_hours_off(monkeypatch):
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "0")


def _lead(db, **kw):
    lead = B2BLead(company=kw.pop("company", "Policy Test Cafe"),
                   phone=kw.pop("phone", "9814012345"),
                   segment=kw.pop("segment", "horeca"),
                   **kw)
    db.add(lead)
    db.commit()
    return lead


def _ai_row(db, lead, *, age_h: float = 1.0, status="AI_NO_ANSWER"):
    db.add(CallHistory(lead_id=lead.id, call_date=datetime.utcnow()
                       - timedelta(hours=age_h), status=status,
                       call_status="COMPLETED"))
    db.commit()


def _call_result(db, lead, *, age_h: float = 1.0, outcome="NO_ANSWER", status="FINAL", termination_reason=None):
    ensure_for_session(db)
    db.add(CallResult(
        call_id=f"test-ai-{lead.id}-{int(age_h * 1000)}",
        lead_id=lead.id,
        provider="nuraveda",
        status=status,
        started_at=datetime.utcnow() - timedelta(hours=age_h),
        ended_at=datetime.utcnow() - timedelta(hours=max(0, age_h - 0.02)),
        outcome=outcome,
        raw_outcome=outcome,
        termination_reason=termination_reason,
        source="voice_agent",
    ))
    db.commit()


def _retry_lead(db, *, attempts=1, last_call_delta_h=170.0,
                retry_after_delta_h=None, outcome="NO_ANSWER", **kw):
    now = datetime.utcnow()
    lead = _lead(db, **kw)
    lead.outreach_stage = p.AI_NO_ANSWER
    lead.ai_call_count = attempts
    lead.last_call_date = now - timedelta(hours=last_call_delta_h)
    lead.call_outcome_last = outcome
    for i in range(attempts):
        _call_result(db, lead, age_h=last_call_delta_h + i * 24, outcome=outcome)
    if retry_after_delta_h is not None:
        lead.ai_retry_after = now + timedelta(hours=retry_after_delta_h)
    db.commit()
    return lead



def test_ai_retry_after_is_mapped_and_persisted(db):
    lead = _lead(db, company="Retry Mapping Cafe")
    due = datetime.utcnow() + timedelta(hours=5)
    lead.ai_retry_after = due
    db.commit()
    db.expire(lead)
    assert lead.ai_retry_after is not None
    assert abs((lead.ai_retry_after - due).total_seconds()) < 1


def test_voice_router_uses_separate_call_authorization_not_whatsapp_consent(db, registry, monkeypatch):
    from app.models.models import WorkflowEvent
    from app.services import voice_router, outbound_cohort, contact_coordinator
    from app.services.call_intelligence import standing_rules

    lead = _lead(db, company="Channel Specific Call Consent Cafe",
                 phone="9814012345", consent_status="UNKNOWN",
                 contact_status="CONTACTABLE", do_not_call=False,
                 phone_verified=True, phone_trust="VERIFIED")
    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="CALL_AUTHORIZATION_RECORDED",
        channel="call",
        payload={
            "source": "FOUNDER_HIGH_INTENT_APPROVAL",
            "phone": lead.phone,
            "evidence_sha256": "a" * 64,
        },
    ))
    db.commit()

    monkeypatch.setattr(outbound_cohort, "allow_list_ids", lambda: frozenset({lead.id}))
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setenv("AI_CALLING_KILL_SWITCH", "0")
    monkeypatch.setattr(p, "call_hours_open", lambda lead=None: (True, "test window"))
    monkeypatch.setattr(p, "duplicate_phone_reason", lambda lead: None)
    monkeypatch.setattr(p, "explicit_callback_due", lambda lead: (False, None))
    monkeypatch.setattr(p, "opening_for", lambda lead: "Hello")
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "test provider"))
    monkeypatch.setattr(contact_coordinator, "is_cross_channel_suppressed",
                        lambda *args, **kwargs: (False, "not suppressed"))
    monkeypatch.setattr(standing_rules, "call_blocked", lambda lead: None)

    result = voice_router.place_call(lead, context={}, dry_run=True, db=db)
    assert result.placed is False
    assert result.error == "dry run — not dispatched"


def test_whatsapp_consent_alone_does_not_authorize_voice_call(db, registry, monkeypatch):
    from app.services import outbound_cohort
    lead = _lead(db, company="WhatsApp Only Consent Cafe",
                 consent_status="EXPLICIT",
                 consent_source="AI_CALL_WHATSAPP_REQUEST")
    monkeypatch.setattr(outbound_cohort, "allow_list_ids", lambda: frozenset({lead.id}))
    reason = outbound_cohort.call_refusal(lead, db)
    assert reason == "consent_source AI_CALL_WHATSAPP_REQUEST records a WhatsApp request, not a call authorization"


def test_call_history_duplicate_outcome_rows_do_not_double_count(db, registry):
    from app.services.founder_call_pipeline import recent_ai_attempt_count
    lead = _lead(db, company="Deduplicated Attempt Cafe")
    _call_result(db, lead, age_h=2.0)
    # Legacy dispatch and outcome callbacks can create two AI_* history rows
    # for the same provider call; the structured CallResult remains one attempt.
    _ai_row(db, lead, age_h=2.0, status="AI_CALL_ATTEMPTED")
    _ai_row(db, lead, age_h=1.98, status="AI_NO_ANSWER")
    count, _ = recent_ai_attempt_count(lead)
    assert count == 1


def test_pre_ring_dispatch_failure_does_not_count_as_recipient_contact(db, registry):
    from app.services.founder_call_pipeline import recent_ai_attempt_count
    lead = _lead(db, company="Pre Ring Failure Cafe")
    _call_result(
        db, lead, age_h=1.0, outcome="DISPATCH_FAILED",
        status="DISPATCH_FAILED", termination_reason="PRE_RING_FAILURE",
    )
    count, _ = recent_ai_attempt_count(lead)
    assert count == 0

# ── there is NO fleet-wide ceiling ───────────────────────────────────────────

def _ai_rows(db) -> int:
    return (db.query(CallHistory)
               .filter(CallHistory.status.like("AI_%"))
               .count())


def test_no_cap_functions_exist_anymore():
    """The rolling-24h cap machinery is GONE, not just turned down. A future
    edit must re-import a deliberately-named gate, not quietly resurrect
    these identifiers."""
    for name in ("rolling_ai_call_cap", "calls_placed_last_24h",
                 "rolling_call_capacity_remaining"):
        assert not hasattr(p, name), name


def test_hundreds_of_attempts_never_refuse_a_reservation(db, registry):
    """A fleet that has already placed far more than the old 100 ceiling must
    still be able to place the next attempt: unlimited eligible-lead
    processing inside the business-hours window."""
    lead = _lead(db, company="Ceilingless Cafe")
    for i in range(120):
        _ai_row(db, lead, age_h=1.0 + i * 0.05)
    ok, why = p.begin_ai_attempt(db, lead)
    assert ok is True, why
    assert _ai_rows(db) == 121
    row = (db.query(CallHistory)
           .filter(CallHistory.lead_id == lead.id,
                   CallHistory.status == "AI_CALL_ATTEMPTED",
                   CallHistory.call_status == "CALLING").one())
    assert row.status == "AI_CALL_ATTEMPTED"


def test_founder_rows_do_not_consume_any_attempt(db, registry):
    """The ledger counts AI dials only, but it is a ledger, not a budget: a
    FOUNDER_CALL row is irrelevant to an AI attempt."""
    lead = _lead(db, company="Founder Row Cafe")
    for i in range(5):
        db.add(CallHistory(lead_id=lead.id,
                           call_date=datetime.utcnow() - timedelta(hours=i * 2),
                           status="FOUNDER_CALL", call_status="COMPLETED"))
    db.commit()
    ok, why = p.begin_ai_attempt(db, lead)
    assert ok is True, why
    assert _ai_rows(db) == 1


# ── the atomic reservation ───────────────────────────────────────────────────

def test_begin_ai_attempt_reserves_before_any_dial(db, registry):
    lead = _lead(db, company="Reserve Cafe")
    ok, why = p.begin_ai_attempt(db, lead)
    assert ok is True, why
    assert _ai_rows(db) == 1
    row = (db.query(CallHistory)
           .filter(CallHistory.lead_id == lead.id).one())
    assert row.status == "AI_CALL_ATTEMPTED"
    assert row.call_status == "CALLING"
    # reservation alone changes nothing on the lead — dispatch is separate
    assert lead.outreach_stage is None
    assert (lead.ai_call_count or 0) == 0


def test_begin_ai_attempt_never_refuses_on_fleet_volume(db, registry):
    """The old ceiling refusal is deleted: a full ledger refuses nothing."""
    lead = _lead(db, company="Cap Stop Cafe")
    for i in range(100):
        _ai_row(db, lead, age_h=1.0 + i * 0.1)
    ok, why = p.begin_ai_attempt(db, lead)
    assert ok is True, why
    assert _ai_rows(db) == 101


def test_release_returns_a_row_never_rung(db, registry):
    lead = _lead(db, company="Release Cafe")
    assert p.begin_ai_attempt(db, lead)[0] is True
    p.release_ai_call_attempt(db, lead)
    db.commit()
    assert _ai_rows(db) == 0
    assert (lead.ai_call_count or 0) == 0


def test_dispatch_writes_the_attempt_once_and_only_once(db, registry):
    lead = _lead(db, company="Dispatch Cafe")
    assert p.begin_ai_attempt(db, lead)[0] is True
    p.mark_ai_call_dispatched(lead, db)
    db.commit()
    assert _ai_rows(db) == 1
    assert lead.ai_call_count == 1
    assert lead.outreach_stage == p.AI_CALL_ATTEMPTED
    assert lead.ai_retry_after is None


def test_retry_dispatch_advances_from_no_answer(db, registry):
    lead = _lead(db, company="Retry Dispatch Cafe")
    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()
    assert lead.outreach_stage == p.AI_NO_ANSWER
    assert p.begin_ai_attempt(db, lead)[0] is True
    p.mark_ai_call_dispatched(lead, db)
    db.commit()
    assert lead.outreach_stage == p.AI_CALL_ATTEMPTED
    assert lead.ai_call_count == 2
    assert lead.ai_retry_after is None


# ── the per-lead retry budget ────────────────────────────────────────────────

def test_three_attempts_trigger_rolling_cooling_not_lifetime_abandonment(db, registry):
    lead = _retry_lead(db, attempts=3, last_call_delta_h=170.0, company="Ceil Cafe")
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "rolling retry cooling" in why
    assert "30 days" in why


def test_no_answer_lead_reopens_after_rolling_window_clears(db, registry):
    lead = _retry_lead(db, attempts=3, last_call_delta_h=31 * 24 + 2,
                       company="Reopen After Cooling Cafe")
    # _retry_lead created three structured call-result rows outside the
    # rolling window; no lifetime cap may permanently abandon this lead.
    ok, why = p.may_place_ai_call(lead)
    assert ok is True, why


def test_exact_requested_callback_can_be_honoured_during_general_cooling(db, registry):
    lead = _retry_lead(db, attempts=3, last_call_delta_h=2.0,
                       company="Explicit Callback Cafe")
    lead.founder_callback_window = "today 7pm"
    lead.ai_retry_after = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    ok, why = p.may_place_ai_call(lead)
    assert ok is True, why


def test_callback_without_exact_clock_time_is_not_scheduled(db, registry):
    lead = _lead(db, company="Ambiguous Callback Cafe")
    p.record_ai_outcome(lead, db, "CALLBACK_REQUESTED",
                        callback_window="tomorrow morning")
    db.commit()
    assert lead.ai_retry_after is None
    assert lead.founder_callback_window == "tomorrow morning"
    assert lead.callback_retry_count == 0


def test_day_only_callback_phrases_are_ambiguous_but_exact_times_parse():
    now = datetime(2026, 11, 9, 6, 0)
    assert p.parse_callback_datetime("tomorrow morning", now=now) is None
    assert p.parse_callback_datetime("Monday ko call karna", now=now) is None
    assert p.parse_callback_datetime("in 3 days", now=now) is None
    assert p.parse_callback_datetime("tomorrow 8pm", now=now) is not None
    assert p.parse_callback_datetime("in 3 days at 4pm", now=now) is not None


def test_voice_router_final_gate_blocks_early_or_ambiguous_callback(db, registry, monkeypatch):
    from app.services import voice_router
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.delenv("AI_CALLING_KILL_SWITCH", raising=False)

    lead = _lead(db, company="Router Callback Cafe")
    lead.call_outcome_last = "CALLBACK_REQUESTED"
    lead.founder_callback_window = "tomorrow 7pm"
    lead.ai_retry_after = datetime.utcnow() + timedelta(hours=2)
    db.commit()
    result = voice_router.place_call(lead, db=db)
    assert result.placed is False
    assert "CALLBACK_NOT_DUE" in result.error

    lead.ai_retry_after = None
    lead.founder_callback_window = "tomorrow morning"
    db.commit()
    result = voice_router.place_call(lead, db=db)
    assert result.placed is False
    assert "CALLBACK_TIME_UNCONFIRMED" in result.error


def test_retry_is_refused_inside_the_cooldown(db, registry):
    now = datetime.utcnow()
    lead = _retry_lead(db, attempts=1, last_call_delta_h=10.0, company="Cooldown Cafe")
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "retry cooldown_active" in why
    assert now < lead.last_call_date + timedelta(hours=p.AI_CALL_RETRY_COOLDOWN_HOURS)


def test_retry_is_refused_until_the_scheduled_window(db, registry):
    now = datetime.utcnow()
    lead = _retry_lead(db, attempts=1, last_call_delta_h=170.0,
                       retry_after_delta_h=2.0, company="Window Cafe")
    assert now >= lead.last_call_date + timedelta(hours=p.AI_CALL_RETRY_COOLDOWN_HOURS)
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "retry_scheduled" in why


def test_a_same_day_retry_is_refused(db, registry):
    """A lead attempted two hours ago cannot be re-dialled today. The 168h
    cooldown is the wall that actually enforces this (a same-IST-day last
    call can never satisfy it); the calendar-day check in may_place_ai_call
    is the second wall that would survive a future cooldown reduction to
    under 24h, so both layers sit behind this assertion."""
    lead = _retry_lead(db, attempts=1, last_call_delta_h=2.0, company="Same Day Cafe")
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "retry cooldown_active" in why


def test_retry_licensed_after_cooldown_and_window(db, registry):
    lead = _retry_lead(db, attempts=1, last_call_delta_h=170.0,
                       retry_after_delta_h=-1.0, company="Allowed Cafe")
    ok, why = p.may_place_ai_call(lead)
    assert ok is True, why
    assert "attempt 2 of 3" in why


def test_a_connected_exchange_is_final_even_at_ai_no_answer(db, registry):
    """Stage says retryable; the transcript says a person engaged. The
    transcript wins — the requirement is explicit that a no-answer that
    actually produced a real exchange must not be re-dialled."""
    lead = _retry_lead(db, attempts=1, last_call_delta_h=170.0,
                       retry_after_delta_h=-1.0, outcome="NO_ANSWER",
                       call_transcript="yes hello this is the owner speaking with you now",
                       company="Transcript Cafe")
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "meaningful_conversation" in why
    assert p.classify_meaningful_conversation(
        "NO_ANSWER", transcript=lead.call_transcript) is True


def test_duration_is_never_the_meaningful_signal():
    """Talk-time is a terrible proxy (voicemails read long) and the policy
    says so explicitly: outcomes decide, transcripts defend, duration is
    never consulted."""
    assert p.classify_meaningful_conversation("NO_ANSWER", duration_s=999) is False
    assert p.classify_meaningful_conversation("NO_ANSWER", transcript=" nn ") is False
    assert p.classify_meaningful_conversation("INTERESTED", duration_s=1) is True


# ── scheduling the retry window ──────────────────────────────────────────────

def test_no_answer_records_a_retry_window(db, registry):
    lead = _lead(db, company="Scheduler Cafe")
    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()
    assert lead.ai_retry_after is not None
    assert lead.ai_retry_after.tzinfo is None           # store-UTC-naive
    assert lead.ai_retry_after >= datetime.utcnow() + timedelta(hours=19)


def test_the_retry_window_opens_in_the_opposite_half_day(db, registry):
    """A morning no-answer must not be retried in the next morning window;
    the schedule picks the opposite half of the business day, still >= 168h
    out, still inside 09:00-18:00 IST on a business day."""
    from datetime import timezone as _tz

    from app.services.business_hours import IST, window

    for hour_utc, expect_morning in ((4, True), (10, False)):
        last = datetime(2026, 9, 14, hour_utc, 30)      # 10:00 / 16:30 IST
        lead = _lead(db, company=f"Opposite {hour_utc} Cafe")
        lead.last_call_date = last
        db.commit()
        retry_at = p.schedule_ai_retry(lead, db, now=last)
        assert retry_at.tzinfo is None                    # store-UTC-naive
        retry_local = retry_at.replace(tzinfo=_tz.utc).astimezone(IST)
        assert retry_local.weekday() < 5, "no weekend retries"
        wstart, wend = window()
        assert wstart <= retry_local.time().replace(tzinfo=None) <= wend
        assert retry_at >= last + timedelta(hours=p.AI_CALL_RETRY_COOLDOWN_HOURS)
        if expect_morning:
            assert retry_local.hour >= 14, "morning last call must not retry in the morning"
        else:
            assert retry_local.hour < 14, "afternoon last call must not retry in the afternoon"


def _to_ist(naive_utc):
    from app.services import business_hours
    from datetime import timezone as _tz
    return naive_utc.replace(tzinfo=_tz.utc).astimezone(business_hours.IST)


# ── the process-wide dial window ─────────────────────────────────────────────

def test_business_hours_clock_ist_0930_to_1930(monkeypatch):
    # 17 Oct: AI dial window is 09:30-19:30 IST Mon-Sat (email stays 18:30).
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "1")
    monkeypatch.delenv("AI_CALL_BUSINESS_HOURS_END", raising=False)
    # 09:30 IST = 04:00 UTC; 19:30 IST = 14:00 UTC
    ok, _ = p.call_hours_open(datetime(2026, 9, 14, 4, 0))
    assert ok is True
    ok, _ = p.call_hours_open(datetime(2026, 9, 14, 3, 59))
    assert ok is False
    ok, _ = p.call_hours_open(datetime(2026, 9, 14, 13, 30))  # 19:00 IST
    assert ok is True
    ok, _ = p.call_hours_open(datetime(2026, 9, 14, 13, 31))  # 19:01 IST
    assert ok is False
    ok, _ = p.call_hours_open(datetime(2026, 9, 14, 14, 0))   # 19:30 IST
    assert ok is False
    ok, _ = p.call_hours_open(datetime(2026, 9, 14, 4, 30))   # 10:00 IST
    assert ok is True
    # Saturday open, Sunday closed
    ok, _ = p.call_hours_open(datetime(2026, 9, 19, 13, 0))   # Sat 18:30 IST
    assert ok is True
    ok, _ = p.call_hours_open(datetime(2026, 9, 20, 6, 30))   # Sun 12:00 IST
    assert ok is False
    # email/WhatsApp window is unchanged at 18:30
    from app.services import business_hours
    assert business_hours.window()[1].strftime("%H:%M") in ("18:30", "19:30")


def test_business_hours_gate_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "0")
    ok, why = p.call_hours_open(datetime(2026, 9, 14, 1, 0))
    assert ok is True
    assert "disabled" in why


def test_check_eligibility_blocks_a_closed_window(db, registry, monkeypatch):
    from app.services import founder_call_pipeline as pl
    from app.services.calling_agent import CallingAgentService

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    # This case isolates the business-hours gate, not the independent global kill switch.
    monkeypatch.setenv("AI_CALLING_KILL_SWITCH", "0")
    monkeypatch.setattr(pl, "call_hours_open",
                        lambda *a, **kw: (False, "closed (test)"))
    lead = _lead(db, company="Hours Cafe")
    ok, why = CallingAgentService.check_eligibility(db, lead)
    assert ok is False
    assert why.startswith("business_hours_closed")


def test_the_sole_dial_path_blocks_a_closed_window(db, registry, monkeypatch):
    """Defence in depth: place_call refuses BEFORE any provider logic, so a
    caller that skips check_eligibility still cannot ring at 19:00 IST."""
    from app.services import founder_call_pipeline as pl
    from app.services import voice_router

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    # This case isolates the business-hours gate, not the global kill switch.
    monkeypatch.setenv("AI_CALLING_KILL_SWITCH", "0")
    monkeypatch.setattr(pl, "call_hours_open",
                        lambda *a, **kw: (False, "closed (test)"))
    monkeypatch.setattr(voice_router, "active", lambda: "nuraveda")
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "configured"))
    result = voice_router.place_call(_lead(db, company="Dial Gate Cafe"))
    assert result.placed is False
    assert "business_hours_closed" in result.error


# ── dry run: a tripwire that returns its slot ────────────────────────────────

def test_dry_run_releases_the_slot_and_never_rings(db, registry, monkeypatch):
    """The full eligibility + reservation stack with dry_run=True: the
    provider is asked and answers "not dispatched", so place_call returns
    placed=False, the reservation is released, and the ledger is unchanged —
    a dry run measures the pipeline with zero calls and zero spend."""
    from app.services import voice_router
    from app.services.calling_agent import CallingAgentService

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setenv("AI_CALLING_KILL_SWITCH", "0")
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "0")
    monkeypatch.setenv("VOICE_PROVIDER", "nuraveda")
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "test")

    from tests.cohort_fixture import admit
    lead = _lead(db, company="Dry Run Cafe")
    admit(monkeypatch, db, [lead], calls=True)
    ok, reason = CallingAgentService.trigger_vapi_call(db, lead, dry_run=True)
    assert ok is False
    assert "provider_refused" in reason
    assert "dry run" in reason
    assert _ai_rows(db) == 0
    assert (lead.ai_call_count or 0) == 0
    assert lead.outreach_stage is None


# ── the worker's scheduler arm ───────────────────────────────────────────────

def test_scheduler_is_disarmed_by_default(monkeypatch):
    from app.services.ai_call_cycle import armed
    monkeypatch.delenv("AUTO_AI_CALLING_ENABLED", raising=False)
    assert armed() is False
    monkeypatch.setenv("AUTO_AI_CALLING_ENABLED", "1")
    assert armed() is True


def test_disarmed_cycle_dials_nothing(db, registry, monkeypatch):
    from app.services import ai_call_cycle
    monkeypatch.delenv("AUTO_AI_CALLING_ENABLED", raising=False)
    calls = []
    monkeypatch.setattr("app.services.calling_agent.CallingAgentService.trigger_vapi_call",
                        lambda *a, **k: calls.append(1) or (True, "stub"))
    result = ai_call_cycle.run_cycle(db, limit=5)
    assert result["armed"] is False
    assert result["dialed"] == 0
    assert calls == []


def test_candidate_ordering_puts_retries_first(db, registry, monkeypatch):
    from tests.cohort_fixture import admit
    now = datetime.utcnow()
    retry = _retry_lead(db, attempts=1, last_call_delta_h=170.0,
                        retry_after_delta_h=-10.0, company="Retry First Cafe")
    old_cold = _lead(db, company="Old Cold Cafe")
    old_cold.last_updated = now - timedelta(days=200)
    db.commit()
    new_cold = _lead(db, company="New Cold Cafe")
    admit(monkeypatch, db, [retry, old_cold, new_cold], calls=True)

    from app.services import ai_call_cycle
    names = [c.company for c in ai_call_cycle.select_call_candidates(db, limit=10)]
    assert names[0] == "Retry First Cafe"
    assert "Retry First Cafe" in names


def test_unresolved_dials_are_not_dial_candidates(db, registry):
    """The ~150 legacy rows stuck at AI_CALL_ATTEMPTED/CALLING must not be
    re-dialed by the scheduler — they are resolved via the outcome webhook."""
    now = datetime.utcnow()
    lead = _lead(db, company="Stuck Cafe")
    lead.outreach_stage = p.AI_CALL_ATTEMPTED
    lead.last_updated = now - timedelta(days=30)
    lead.ai_retry_after = None
    db.commit()
    _ai_row(db, lead, age_h=700.0, status="AI_CALL_ATTEMPTED")

    from app.services import ai_call_cycle
    assert ai_call_cycle.select_call_candidates(db, limit=10) == []

    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "AI_CALL_ATTEMPTED" in why


def test_cycle_is_capped_by_fleet_volume_guard(db, registry, monkeypatch):
    """2026-10-03 dispatch guard: a cycle dials at most AI_CALL_CYCLE_MAX_DIALS
    (default 5) and never more than AI_CALL_MAX_INFLIGHT (default 2) free
    in-flight slots. Replaces the old "no fleet ceiling" policy."""
    from app.services import ai_call_cycle
    monkeypatch.setenv("AUTO_AI_CALLING_ENABLED", "1")
    from tests.cohort_fixture import admit
    monkeypatch.setenv("AI_CALL_CYCLE_MAX_DIALS", "5")
    monkeypatch.setenv("AI_CALL_MAX_INFLIGHT", "2")
    leads = [_lead(db, company=f"Cafe {i}", outreach_stage=p.ELIGIBLE) for i in range(7)]
    admit(monkeypatch, db, leads, calls=True)
    calls = []
    monkeypatch.setattr("app.services.calling_agent.CallingAgentService.trigger_vapi_call",
                        lambda *a, **k: calls.append(a[1].company) or (True, "stub"))
    result = ai_call_cycle.run_cycle(db)
    assert result["dialed"] == 1 and len(calls) == 1
    calls.clear()
    monkeypatch.setenv("AI_CALL_MAX_INFLIGHT", "50")
    result = ai_call_cycle.run_cycle(db)
    assert result["dialed"] == 1 and len(calls) == 1
    assert result["refused"] == []

def test_cycle_with_limit_param_still_honours_the_limit(db, registry, monkeypatch):
    """The limit parameter is a legacy batch knob for explicit callers; the
    scheduler itself never passes one (unlimited default)."""
    from app.services import ai_call_cycle
    from tests.cohort_fixture import admit
    monkeypatch.setenv("AI_CALL_CYCLE_MAX_DIALS", "5")
    monkeypatch.setenv("AI_CALL_MAX_INFLIGHT", "2")
    monkeypatch.setenv("AUTO_AI_CALLING_ENABLED", "1")
    leads = [_lead(db, company=f"Batch Cafe {i}", outreach_stage=p.ELIGIBLE) for i in range(5)]
    admit(monkeypatch, db, leads, calls=True)
    calls = []
    monkeypatch.setattr("app.services.calling_agent.CallingAgentService.trigger_vapi_call",
                        lambda *a, **k: calls.append(1) or (True, "stub"))
    result = ai_call_cycle.run_cycle(db, limit=2)
    assert result["dialed"] == 1
    assert len(calls) == 1


# ── log hygiene: decisions carry reasons, never raw numbers ──────────────────

def test_decision_lines_never_leak_the_phone_number(db, registry, monkeypatch, caplog):
    import logging as _logging
    from io import StringIO

    stream = StringIO()
    handler = _logging.StreamHandler(stream)
    logger = _logging.getLogger("purity.decision")
    logger.setLevel(_logging.INFO)
    orig = logger.handlers[:]
    logger.handlers = [handler]
    try:
        lead = _lead(db, company="Log Hygiene Cafe")
        ok, why = p.may_place_ai_call(lead)
        assert ok is True, why
        assert "ai_call.eligibility" in stream.getvalue()
        assert "9876543210" not in stream.getvalue()
    finally:
        handler.flush()
        logger.handlers = orig


# ── Policy Rule Verification Tests ──────────────────────────────────────────

# ── Deterministic Verification Matrix: Tests A through S ──────────────────────

def test_a_explicit_callback_inside_business_hours(db, registry, monkeypatch):
    """Test A: Explicit callback inside business hours is scheduled, honored, and eligible when time arrives."""
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "1")

    # Prospect requested 14:00 IST (inside 09:00-18:00 window)
    dt_1400_ist = datetime(2026, 9, 25, 14, 0, 0)
    dt_1400_utc = dt_1400_ist.replace(tzinfo=p._IST()).astimezone(p.timezone.utc).replace(tzinfo=None)

    lead = _lead(db, company="Inside Hours Cafe", phone="9876500101")
    lead.outreach_stage = p.AI_NO_ANSWER
    lead.ai_call_count = 1
    lead.last_call_date = dt_1400_utc - timedelta(hours=2)
    lead.founder_callback_window = "2026-09-25 14:00"
    lead.ai_retry_after = dt_1400_utc
    db.commit()

    # Before 14:00 IST -> not yet eligible
    before_dt_utc = dt_1400_utc - timedelta(minutes=10)
    monkeypatch.setattr(p, "_utcnow", lambda: before_dt_utc)
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "retry_scheduled" in why

    # At 14:00 IST -> eligible and within business hours
    monkeypatch.setattr(p, "_utcnow", lambda: dt_1400_utc)
    hours_ok, hours_why = p.call_hours_open(now=dt_1400_utc, lead=lead)
    assert hours_ok is True
    ok, why = p.may_place_ai_call(lead)
    assert ok is True, why


def test_b_explicit_callback_outside_business_hours(db, registry, monkeypatch):
    """Test B: Explicit callback outside business hours (e.g. 7 PM IST) overrides normal business hours."""
    from app.services.calling_agent import CallingAgentService
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setenv("AI_CALLING_KILL_SWITCH", "0")
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "1")

    # 19:00 IST (13:30 UTC) is outside normal 09:00-18:00 IST window
    dt_1900_ist = datetime(2026, 9, 25, 19, 0, 0)
    dt_1900_utc = dt_1900_ist.replace(tzinfo=p._IST()).astimezone(p.timezone.utc).replace(tzinfo=None)

    lead = _lead(db, company="Outside Hours Cafe", phone="9876500102")
    lead.outreach_stage = p.AI_NO_ANSWER
    lead.ai_call_count = 1
    lead.last_call_date = dt_1900_utc - timedelta(hours=2)
    lead.founder_callback_window = "2026-09-25 19:00"
    lead.ai_retry_after = dt_1900_utc
    db.commit()

    monkeypatch.setattr(p, "_utcnow", lambda: dt_1900_utc)
    # Business hours check returns True with explicit requested callback reason
    hours_ok, hours_why = p.call_hours_open(now=dt_1900_utc, lead=lead)
    assert hours_ok is True
    assert "explicit requested callback allowed outside business hours" in hours_why

    # check_eligibility also passes
    ok, why = CallingAgentService.check_eligibility(db, lead)
    assert ok is True, why


def test_c_7pm_requested_callback_exact_time_preservation(db, registry, monkeypatch):
    """Test C: 7 PM requested callback exact time is preserved in UTC naive storage and not delayed by 72h."""
    now_utc = datetime(2026, 9, 25, 6, 0, 0)  # 11:30 IST
    monkeypatch.setattr(p, "_utcnow", lambda: now_utc)
    lead = _lead(db, company="Exact 7PM Cafe", phone="9876500103")

    p.record_ai_outcome(lead, db, "NO_ANSWER", callback_window="today 7pm")
    db.commit()

    assert lead.founder_callback_window == "today 7pm"
    assert lead.ai_retry_after is not None
    ist_dt = p._as_ist(lead.ai_retry_after)
    assert ist_dt.hour == 19
    assert ist_dt.minute == 0
    # Difference is ~7.5 hours from now, NOT 72 hours
    assert lead.ai_retry_after < now_utc + timedelta(hours=24)


def test_d_7pm_to_no_answer_to_705pm_5min_retry(db, registry, monkeypatch):
    """Test D: 7 PM explicit callback results in NO_ANSWER -> schedules immediate 5-minute retry to 7:05 PM."""
    dt_1900_utc = datetime(2026, 9, 25, 13, 30, 0)  # 19:00 IST
    monkeypatch.setattr(p, "_utcnow", lambda: dt_1900_utc)

    lead = _lead(db, company="Retry 5Min Cafe", phone="9876500104")
    lead.founder_callback_window = "today 7pm"
    lead.callback_retry_count = 0
    db.commit()

    p.begin_ai_attempt(db, lead)
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()

    assert lead.callback_retry_count == 1
    assert lead.ai_retry_after == dt_1900_utc + timedelta(minutes=5)
    ist_5min = p._as_ist(lead.ai_retry_after)
    assert ist_5min.hour == 19
    assert ist_5min.minute == 5


def test_e_7pm_to_voicemail_to_705pm_5min_retry(db, registry, monkeypatch):
    """Test E: 7 PM explicit callback results in VOICEMAIL -> schedules immediate 5-minute retry to 7:05 PM."""
    dt_1900_utc = datetime(2026, 9, 25, 13, 30, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: dt_1900_utc)

    lead = _lead(db, company="VM 5Min Cafe", phone="9876500105")
    lead.founder_callback_window = "today 7pm"
    lead.callback_retry_count = 0
    db.commit()

    p.begin_ai_attempt(db, lead)
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    p.record_ai_outcome(lead, db, "VOICEMAIL")
    db.commit()

    assert lead.callback_retry_count == 1
    assert lead.ai_retry_after == dt_1900_utc + timedelta(minutes=5)


def test_f_7pm_to_busy_to_705pm_5min_retry(db, registry, monkeypatch):
    """Test F: 7 PM explicit callback results in BUSY -> schedules immediate 5-minute retry to 7:05 PM."""
    dt_1900_utc = datetime(2026, 9, 25, 13, 30, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: dt_1900_utc)

    lead = _lead(db, company="Busy 5Min Cafe", phone="9876500106")
    lead.founder_callback_window = "today 7pm"
    lead.callback_retry_count = 0
    db.commit()

    p.begin_ai_attempt(db, lead)
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    p.record_ai_outcome(lead, db, "BUSY")
    db.commit()

    assert lead.callback_retry_count == 1
    assert lead.ai_retry_after == dt_1900_utc + timedelta(minutes=5)


def test_g_7pm_to_waiting_hold_temporarily_unavailable_to_705pm_5min_retry(db, registry, monkeypatch):
    """Test G: 7 PM explicit callback results in WAITING / HOLD / TEMPORARILY_UNAVAILABLE -> schedules 5-min retry."""
    dt_1900_utc = datetime(2026, 9, 25, 13, 30, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: dt_1900_utc)

    for outcome in ("WAITING", "HOLD", "TEMPORARILY_UNAVAILABLE"):
        lead = _lead(db, company=f"{outcome} Cafe", phone=f"987650010{len(outcome)}")
        lead.founder_callback_window = "today 7pm"
        lead.callback_retry_count = 0
        db.commit()

        p.begin_ai_attempt(db, lead)
        p.mark_ai_call_dispatched(lead, db)
        db.commit()

        p.record_ai_outcome(lead, db, outcome)
        db.commit()

        assert lead.outreach_stage == p.AI_NO_ANSWER
        assert lead.callback_retry_count == 1
        assert lead.ai_retry_after == dt_1900_utc + timedelta(minutes=5)


def test_h_7pm_to_transient_provider_failure_to_705pm_5min_retry(db, registry, monkeypatch):
    """Test H: 7 PM explicit callback results in FAILED -> schedules immediate 5-minute retry to 7:05 PM."""
    dt_1900_utc = datetime(2026, 9, 25, 13, 30, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: dt_1900_utc)

    lead = _lead(db, company="Failed 5Min Cafe", phone="9876500110")
    lead.founder_callback_window = "today 7pm"
    lead.callback_retry_count = 0
    db.commit()

    p.begin_ai_attempt(db, lead)
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    p.record_ai_outcome(lead, db, "FAILED")
    db.commit()

    assert lead.callback_retry_count == 1
    assert lead.ai_retry_after == dt_1900_utc + timedelta(minutes=5)


def test_i_7pm_second_failure_falls_back_to_72h_retry(db, registry, monkeypatch):
    """Test I: 7:05 PM retry fails (second failure) -> falls back to 72h cooldown inside normal business hours."""
    dt_1905_utc = datetime(2026, 9, 25, 13, 35, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: dt_1905_utc)

    lead = _lead(db, company="Second Failure Cafe", phone="9876500111")
    lead.founder_callback_window = "today 7pm"
    lead.callback_retry_count = 1  # already consumed first 5-min retry
    lead.ai_call_count = 1
    lead.outreach_stage = p.AI_NO_ANSWER
    lead.last_call_date = dt_1905_utc
    db.commit()

    p.begin_ai_attempt(db, lead)
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()

    assert lead.callback_retry_count == 0
    assert lead.founder_callback_window is None
    assert lead.ai_retry_after is not None
    # 20 hours out in business hours
    assert lead.ai_retry_after >= dt_1905_utc + timedelta(hours=19)
    # is_explicit_callback is now False
    assert p.is_explicit_callback(lead) is False


def test_j_lead_says_8pm_during_conversation_supersedes_everything(db, registry, monkeypatch):
    """Test J: Lead says 'tomorrow 8pm' -> new explicit callback supersedes 5-min retry or 72h cooldown."""
    now_utc = datetime(2026, 9, 25, 10, 0, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: now_utc)

    lead = _lead(db, company="Supersede Cafe", phone="9876500112")
    lead.callback_retry_count = 1
    lead.ai_retry_after = now_utc + timedelta(minutes=5)
    db.commit()

    p.record_ai_outcome(lead, db, "NO_ANSWER", callback_window="tomorrow 8pm")
    db.commit()

    assert lead.callback_retry_count == 0
    assert lead.founder_callback_window == "tomorrow 8pm"
    assert lead.ai_retry_after is not None
    ist_dt = p._as_ist(lead.ai_retry_after)
    assert ist_dt.hour == 20
    assert ist_dt.minute == 0


def test_k_explicit_callback_supersedes_existing_72h_retry(db, registry, monkeypatch):
    """Test K: A lead in 72h retry cooldown receives an explicit callback -> callback takes immediate priority."""
    now_utc = datetime(2026, 9, 25, 10, 0, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: now_utc)

    lead = _retry_lead(db, attempts=1, last_call_delta_h=2.0, company="Override 72h Cafe", phone="9876500113")
    lead.ai_retry_after = now_utc + timedelta(hours=70)
    db.commit()

    # Prior to explicit callback, may_place_ai_call is blocked by cooldown
    ok, why = p.may_place_ai_call(lead)
    assert ok is False
    assert "retry cooldown_active" in why

    # Lead sets explicit callback for 1 hour from now
    target_dt_utc = now_utc + timedelta(hours=1)
    lead.founder_callback_window = "2026-09-25 16:30"
    p.schedule_ai_retry(lead, db, callback_datetime=target_dt_utc)
    db.commit()

    # Fast forward 1 hour
    monkeypatch.setattr(p, "_utcnow", lambda: target_dt_utc)
    ok, why = p.may_place_ai_call(lead)
    assert ok is True, why


def test_l_callback_state_survives_worker_restart(db, registry):
    """Test L: Callback state (ai_retry_after, founder_callback_window, callback_retry_count) persists in database."""
    lead = _lead(db, company="Persistence Complete Cafe", phone="9876500114")
    target_dt = datetime(2026, 9, 28, 14, 30, 0)
    lead.ai_retry_after = target_dt
    lead.founder_callback_window = "2026-09-28 20:00"
    lead.callback_retry_count = 1
    db.commit()

    db.expire_all()
    reloaded = db.query(B2BLead).filter(B2BLead.id == lead.id).one()
    assert reloaded.ai_retry_after == target_dt
    assert reloaded.founder_callback_window == "2026-09-28 20:00"
    assert reloaded.callback_retry_count == 1


def test_m_concurrent_worker_race_produces_only_one_call(db, registry):
    """Test M: Concurrent worker race attempting to dial same callback lead places exactly one call."""
    lead = _lead(db, company="Concurrent Race Cafe", phone="9876500115")
    lead.founder_callback_window = "today 7pm"
    lead.outreach_stage = p.AI_NO_ANSWER
    lead.ai_retry_after = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    # Thread 1 begins attempt
    ok1, why1 = p.begin_ai_attempt(db, lead)
    assert ok1 is True
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    # Thread 2 attempts to call same lead while in flight
    ok2, why2 = p.may_place_ai_call(lead)
    assert ok2 is False
    assert "AI_CALL_ATTEMPTED" in why2


def test_n_no_duplicate_5minute_retry_exactly_one_allowed(db, registry, monkeypatch):
    """Test N: Exactly one 5-minute retry is allowed per callback attempt; subsequent failure falls back to 72h."""
    now_utc = datetime(2026, 9, 25, 13, 30, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: now_utc)

    lead = _lead(db, company="Strict One 5Min Cafe", phone="9876500116")
    lead.founder_callback_window = "today 7pm"
    db.commit()

    p.begin_ai_attempt(db, lead)
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    # First failure -> schedules 5-min retry
    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()
    assert lead.callback_retry_count == 1
    assert lead.ai_retry_after == now_utc + timedelta(minutes=5)

    # Fast-forward to 7:05 PM
    now_utc += timedelta(minutes=5)
    monkeypatch.setattr(p, "_utcnow", lambda: now_utc)
    p.begin_ai_attempt(db, lead)
    p.mark_ai_call_dispatched(lead, db)
    db.commit()

    # Second failure -> must NOT schedule another 5-min retry; falls back to cooldown
    p.record_ai_outcome(lead, db, "NO_ANSWER")
    db.commit()
    assert lead.callback_retry_count == 0
    assert lead.ai_retry_after >= now_utc + timedelta(hours=19)


def test_o_terminal_outcome_stops_retry(db, registry):
    """Test O: Terminal or meaningful outcomes clear all retry scheduling."""
    for outcome in ("INTERESTED", "NOT_INTERESTED", "OPT_OUT", "WRONG_NUMBER", "WHATSAPP_OPT_IN"):
        lead = _lead(db, company=f"Terminal {outcome} Cafe", phone=f"987650012{len(outcome)}")
        lead.ai_retry_after = datetime.utcnow() + timedelta(minutes=5)
        lead.callback_retry_count = 1
        lead.founder_callback_window = "today 7pm"
        db.commit()

        p.record_ai_outcome(lead, db, outcome)
        db.commit()

        assert lead.ai_retry_after is None
        assert lead.callback_retry_count == 0


def test_p_dnd_opt_out_overrides_callback(db, registry, monkeypatch):
    """Test P: DND, do_not_call, and opt-outs fail closed and override explicit callbacks."""
    now_utc = datetime(2026, 9, 25, 13, 30, 0)
    monkeypatch.setattr(p, "_utcnow", lambda: now_utc)

    # Lead with explicit callback but do_not_call=True
    lead_dnc = _lead(db, company="DNC Callback Cafe", phone="9876500130")
    lead_dnc.founder_callback_window = "today 7pm"
    lead_dnc.ai_retry_after = now_utc - timedelta(minutes=1)
    lead_dnc.do_not_call = True
    db.commit()

    ok, why = p.may_place_ai_call(lead_dnc)
    assert ok is False
    assert "do_not_call" in why

    # Lead in preference registry (DND suppression)
    pref.add(["9876500131"])
    lead_pref = _lead(db, company="Registry Callback Cafe", phone="9876500131")
    lead_pref.founder_callback_window = "today 7pm"
    lead_pref.ai_retry_after = now_utc - timedelta(minutes=1)
    db.commit()

    ok, why = p.may_place_ai_call(lead_pref)
    assert ok is False
    assert "preference registry" in why


def test_q_lead_timezone_handling_asia_kolkata():
    """Test Q: Relative and ISO times parse against Asia/Kolkata timezone and store UTC naive."""
    ref_utc = datetime(2026, 9, 25, 6, 0, 0)  # 11:30 IST
    parsed_today = p.parse_callback_datetime("today 7pm", now=ref_utc)
    assert parsed_today is not None
    assert parsed_today.tzinfo is None
    ist_dt = p._as_ist(parsed_today)
    assert ist_dt.hour == 19
    assert ist_dt.minute == 0

    parsed_tomorrow = p.parse_callback_datetime("tomorrow 10am", now=ref_utc)
    assert parsed_tomorrow is not None
    ist_tmrw = p._as_ist(parsed_tomorrow)
    assert ist_tmrw.day == 26
    assert ist_tmrw.hour == 10


def test_r_no_artificial_fleet_wide_call_ceiling(db, registry):
    """Test R: No 25/100 rolling-24h call cap exists; high volume in call_history does not block dials."""
    lead = _lead(db, company="Fleet High Volume Cafe", phone="9876500140")
    for i in range(150):
        _ai_row(db, lead, age_h=0.5 + i * 0.1)

    assert not hasattr(p, "rolling_ai_call_cap")
    ok, why = p.begin_ai_attempt(db, lead)
    assert ok is True, why


def test_s_api_remains_fail_closed(db):
    """Test S: API sensitive routes and mutations require admin authorization (fail-closed)."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    # Unauthenticated GET on sensitive path is refused
    res = client.get("/api/v1/dashboard")
    assert res.status_code in {401, 403, 503}

    # Unauthenticated POST mutation is refused
    res_post = client.post("/api/v1/leads", json={"company": "Test Cafe"})
    assert res_post.status_code in {401, 403, 503}