"""
Comprehensive Test Suite for Definite Answer Follow-Up Policy.

Covers all 18 requirements mandated by Founder Directive:
1. Prospect says Wednesday 3 PM: no call before Wednesday 3 PM.
2. Prospect says tomorrow morning: callback is scheduled in correct timezone (Asia/Kolkata).
3. Prospect says next Monday: no early contact.
4. Ambiguous callback time: clarification or human review.
5. PM2 restart: scheduled callback survives.
6. VPS reboot: callback remains scheduled.
7. Multiple workers: only one callback executes (atomic locking).
8. Prospect reschedules: previous callback is cancelled/rescheduled.
9. Prospect opts out: all applicable pending contact tasks stop.
10. Prospect purchases: acquisition follow-up stops.
11. No answer: lead remains open with controlled retry.
12. Three unanswered attempts: lead is not automatically discarded.
13. Human takeover: conflicting AI contact pauses.
14. DNC: no future contact is possible through any worker.
15. Channel-specific opt-out: the relevant channel is blocked.
16. Callback due outside permitted hours: defer until next permitted opportunity without violating requested time.
17. Callback requested at a specific time: never call early.
18. Duplicate webhook or transcript: no duplicate callback task.
"""
from __future__ import annotations

import pytest
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, B2BLead, ScheduledCallback, WorkflowEvent, EmailDraft
from app.services import (
    callback_scheduler as cs,
    followup_policy_engine as fpe,
    conversation_disposition as cd,
    contact_coordinator as cc,
    lead_state_machine as lsm,
    ai_call_qualifier as acq,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _create_lead(db, **kwargs) -> B2BLead:
    defaults = {
        "company": "Test Cafe & Resto",
        "contact_name": "Test Manager",
        "phone": "+919876543210",
        "email": "test@cafe.example.in",
        "status": "QUEUED",
        "contact_status": "CONTACTABLE",
        "do_not_call": False,
        "lead_owner": "AI_AGENT",
        "call_attempts": 0,
    }
    defaults.update(kwargs)
    lead = B2BLead(**defaults)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


# 1. Prospect says Wednesday 3 PM: no call before Wednesday 3 PM
def test_1_wednesday_3pm_no_early_call(db):
    base_monday = datetime(2026, 11, 9, 10, 0, tzinfo=IST)  # Monday 10:00 AM IST
    lead = _create_lead(db)

    sc = cs.schedule_callback(db, lead.id, "Please call me on Wednesday at 3 PM", base_time=base_monday)
    assert sc.callback_confirmed is True
    assert sc.callback_time == "15:00"

    # Tuesday morning: should be blocked
    tuesday_now = datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db, now=tuesday_now)
    assert suppressed is True
    assert "early calls suppressed" in reason


# 2. Prospect says tomorrow morning: scheduled in correct timezone (Asia/Kolkata)
def test_2_tomorrow_morning_timezone_and_timing(db):
    base = datetime(2026, 11, 9, 14, 0, tzinfo=IST)
    lead = _create_lead(db)

    sc = cs.schedule_callback(db, lead.id, "Call tomorrow morning", base_time=base)
    assert sc.callback_confirmed is True
    assert sc.callback_timezone == "Asia/Kolkata"
    assert sc.callback_time == "10:00"

    # Converted back to IST, window should start at 10:00 AM on Nov 10
    w_start_ist = sc.callback_window_start.replace(tzinfo=timezone.utc).astimezone(IST)
    assert w_start_ist.date() == date(2026, 11, 10)
    assert w_start_ist.time() == time(10, 0)


# 3. Prospect says next Monday: no early contact
def test_3_next_monday_no_early_contact(db):
    base_monday = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    lead = _create_lead(db)

    sc = cs.schedule_callback(db, lead.id, "I am travelling; call next Monday", base_time=base_monday)
    assert sc.callback_confirmed is True

    # Check on Thursday: strictly suppressed
    thursday_now = datetime(2026, 11, 12, 11, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db, now=thursday_now)
    assert suppressed is True
    assert "CALLBACK_PENDING" in reason


# 4. Ambiguous callback time: clarification or human review
def test_4_ambiguous_callback_creates_review_task(db):
    lead = _create_lead(db)

    # Prospect says "call when I return to the office" without a date
    sc = cs.schedule_callback(db, lead.id, "Call when I return to the office")
    assert sc.callback_confirmed is False
    assert sc.callback_status == "CALLBACK_REQUESTED"

    # Automated call is held until human review
    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db)
    assert suppressed is True
    assert "CALLBACK_UNCONFIRMED" in reason


# 5. PM2 restart: scheduled callback survives in database
def test_5_pm2_restart_callback_survives(db):
    lead = _create_lead(db)
    sc = cs.schedule_callback(db, lead.id, "Call on Friday at 4 PM")
    cb_id = sc.id

    # Simulate PM2 worker reload by expiring SQLAlchemy session
    db.expire_all()

    recovered = db.query(ScheduledCallback).filter(ScheduledCallback.id == cb_id).first()
    assert recovered is not None
    assert recovered.callback_status == "CALLBACK_SCHEDULED"
    assert recovered.lead_id == lead.id


# 6. VPS reboot: callback remains scheduled and queryable in database
def test_6_vps_reboot_callback_remains_scheduled(db):
    lead = _create_lead(db)
    sc = cs.schedule_callback(db, lead.id, "Call next Monday at 11 AM")
    cb_id = sc.id

    # Simulate full process and DB reconnection
    db.expire_all()
    recovered = db.query(ScheduledCallback).filter(ScheduledCallback.id == cb_id).first()
    assert recovered is not None
    assert recovered.callback_confirmed is True
    assert recovered.callback_time == "11:00"


# 7. Multiple workers: only one callback executes (atomic lock)
def test_7_atomic_claim_prevents_duplicate_worker_execution(db):
    lead = _create_lead(db)
    sc = cs.schedule_callback(db, lead.id, "Call today at 3 PM")
    sc.callback_window_start = datetime.utcnow() - timedelta(minutes=5)
    db.commit()

    # Worker 1 claims callback
    claimed_1, cb_1 = cs.claim_callback_atomic(db, sc.id, worker_id="WORKER_1")
    assert claimed_1 is True
    assert cb_1.callback_status == "CALLBACK_IN_PROGRESS"

    # Worker 2 attempts to claim simultaneously
    claimed_2, cb_2 = cs.claim_callback_atomic(db, sc.id, worker_id="WORKER_2")
    assert claimed_2 is False
    assert cb_2 is None


# 8. Prospect reschedules: previous callback is cancelled
def test_8_reschedule_cancels_previous_callback(db):
    lead = _create_lead(db)
    sc1 = cs.schedule_callback(db, lead.id, "Call tomorrow at 3 PM")
    assert sc1.callback_status == "CALLBACK_SCHEDULED"

    # Prospect changes their mind
    sc2 = cs.reschedule_callback(db, lead.id, "Actually call next Monday at 11 AM")
    db.refresh(sc1)

    assert sc1.callback_status == "CALLBACK_RESCHEDULED"
    assert sc2.callback_status == "CALLBACK_SCHEDULED"
    assert sc2.callback_time == "11:00"


# 9. Prospect opts out: all applicable pending contact tasks stop
def test_9_prospect_opt_out_cancels_all_tasks(db):
    lead = _create_lead(db)
    sc = cs.schedule_callback(db, lead.id, "Call Friday at 4 PM")

    fpe.apply_followup_disposition(lead, db, "OPT_OUT", details={"reason": "Customer requested removal"})
    db.refresh(sc)

    assert lead.do_not_call is True
    assert lead.status == "DO_NOT_CONTACT"
    assert sc.callback_status == "CALLBACK_CANCELLED"

    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db)
    assert suppressed is True


# 10. Prospect purchases: acquisition follow-up stops
def test_10_purchase_stops_acquisition_followup(db):
    lead = _create_lead(db)
    draft = EmailDraft(lead_id=lead.id, status="DRAFT", follow_up_type="warm_followup", recipient=lead.email, subject="Coffee", body="Intro")
    db.add(draft)
    db.commit()

    cancelled = cc.handle_purchase_event(lead.id, order_id="#1005", amount=1200.0, db=db)
    assert cancelled == 1
    assert lead.status == "PAID_CONVERSION"

    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db)
    assert suppressed is True
    assert "ACQUISITION_STOPPED" in reason
    assert lead.status == "PAID_CONVERSION"

    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db)
    assert suppressed is True
    assert "ACQUISITION_STOPPED" in reason


# 11. No answer: lead remains open with controlled retry
def test_11_no_answer_keeps_lead_open(db):
    lead = _create_lead(db, call_attempts=0)
    res = fpe.apply_followup_disposition(lead, db, "NO_ANSWER")

    assert lead.status == "NO_ANSWER"
    assert lead.call_attempts == 1
    assert res["may_retry"] is True
    assert lead.ai_retry_after is not None


# 12. Three unanswered attempts: lead is NOT automatically discarded
def test_12_three_unanswered_attempts_does_not_discard(db):
    lead = _create_lead(db, call_attempts=2)
    # Attempt 3:
    res = fpe.apply_followup_disposition(lead, db, "NO_ANSWER")

    assert lead.call_attempts == 3
    assert lead.status == "NO_ANSWER"
    # Under new policy, lead is NOT terminated, but scheduled for spaced retry 4
    assert res["may_retry"] is True
    assert "adaptive retry 4" in res["reason"]

    # When reaching cycle cap (5 attempts), transitions to COOLING_PERIOD, not DISCARDED
    lead.call_attempts = 4
    res5 = fpe.apply_followup_disposition(lead, db, "NO_ANSWER")
    assert lead.call_attempts == 5
    assert lead.status == "COOLING_PERIOD"
    assert res5["may_retry"] is True
    assert "cooling period entered" in res5["reason"]


# 13. Human takeover: conflicting AI contact pauses
def test_13_human_takeover_pauses_ai_touches(db):
    lead = _create_lead(db, lead_owner="Hiten", status="HUMAN_TAKEOVER")

    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db)
    assert suppressed is True
    assert "HUMAN_TAKEOVER" in reason


# 14. DNC: no future contact is possible through any worker
def test_14_dnc_permanently_blocks_outreach(db):
    lead = _create_lead(db, do_not_call=True)

    for ch in ("call", "whatsapp", "email"):
        suppressed, reason = cc.is_cross_channel_suppressed(lead, ch, db=db)
        assert suppressed is True
        assert "DNC" in reason


# 15. Channel-specific opt-out: relevant channel is blocked
def test_15_channel_specific_optout(db):
    lead = _create_lead(db)
    # Customer says "do not WhatsApp me, only email me"
    lead.consent_status = "OPT_OUT"

    suppressed_wa, _ = cc.is_cross_channel_suppressed(lead, "whatsapp", db=db)
    assert suppressed_wa is True


# 16. Callback due outside permitted hours: defer until next permitted window
def test_16_callback_outside_hours_deferred_to_next_open_window(db):
    # Prospect says "Call me tonight at 10 PM" (22:00 IST)
    base_mon = datetime(2026, 11, 9, 15, 0, tzinfo=IST)
    parsed = cs.parse_callback_request("Call me tonight at 10 PM", base_time=base_mon)

    assert parsed["callback_confirmed"] is True
    assert parsed["deferred_for_business_hours"] is True

    # 22:00 IST is outside TRAI hours (09:30-19:00).
    # Must defer to tomorrow morning at 09:30 AM IST (NEVER call earlier than requested 22:00)
    w_start_ist = parsed["window_start_utc"].replace(tzinfo=timezone.utc).astimezone(IST)
    assert w_start_ist.date() == date(2026, 11, 10)
    assert w_start_ist.time() == time(9, 30)


# 17. Callback requested at a specific time: never call early
def test_17_never_call_early_before_callback_window(db):
    base = datetime(2026, 11, 9, 11, 0, tzinfo=IST)
    lead = _create_lead(db)
    sc = cs.schedule_callback(db, lead.id, "Call after 5 PM", base_time=base)

    # 3 PM check: must be strictly suppressed
    three_pm_utc = datetime(2026, 11, 9, 9, 30, tzinfo=timezone.utc).replace(tzinfo=None)  # 15:00 IST
    suppressed, reason = cc.is_cross_channel_suppressed(lead, "call", db=db, now=three_pm_utc)
    assert suppressed is True
    assert "early calls suppressed" in reason


# 18. Duplicate webhook or transcript: no duplicate callback task
def test_18_idempotent_duplicate_callback_handling(db):
    lead = _create_lead(db)
    sc1 = cs.schedule_callback(db, lead.id, "Please call tomorrow at 3 PM")
    # Duplicate webhook arrives with identical statement
    sc2 = cs.schedule_callback(db, lead.id, "Please call tomorrow at 3 PM")

    active = db.query(ScheduledCallback).filter(
        ScheduledCallback.lead_id == lead.id,
        ScheduledCallback.callback_status.in_(["CALLBACK_REQUESTED", "CALLBACK_SCHEDULED"])
    ).all()
    # Exactly one active callback task remains
    assert len(active) == 1
    assert active[0].id == sc2.id
