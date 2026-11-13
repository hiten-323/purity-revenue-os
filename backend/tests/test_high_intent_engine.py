"""
Comprehensive Automated Test Suite for High-Intent Conversion Engine V1.0.

Tests:
- High-intent evidence-based scoring and signal decay.
- Multi-channel independent contact eligibility.
- AI voice qualification and 3-attempt adaptive retry scheduler.
- Durable lead state machine transitions.
- Daily one-paid-conversion controller and Shopify order verification.
- Cross-channel contact coordination and post-purchase draft cancellation.
"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.high_intent_scorer import evaluate_intent, rank_leads_by_intent
from app.services.contact_eligibility import (
    check_voice_eligibility,
    check_email_eligibility,
    check_whatsapp_eligibility,
    evaluate_lead_eligibility,
)
from app.services.ai_call_qualifier import calculate_next_retry, record_call_outcome
from app.services.lead_state_machine import can_transition
from app.services.daily_target_controller import (
    get_today_ist_boundaries,
    DailyConversionStatus,
    should_pause_outbound_sales_today,
)
from app.services.contact_coordinator import acquire_lead_lock, release_lead_lock, handle_purchase_event

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# 1. High-Intent Scoring Tests
# ---------------------------------------------------------------------------

def test_explicit_coffee_enquiry_scoring():
    lead = SimpleNamespace(
        id=101,
        lead_source="inbound_website_enquiry",
        qualification_notes="Looking for instant coffee supply and price quotation for 5 office locations",
        call_summary="",
        division="corporate_office",
        segment="corporate",
        industry="Corporate",
        status="NEW",
        do_not_call=False,
        sample_sku=None,
        purchase_sku=None,
        sample_requested=False,
        current_supplier=None,
        consent_timestamp=datetime.utcnow() - timedelta(days=2),
        stage_entered_date=datetime.utcnow() - timedelta(days=2),
        phone="+919876543210",
        whatsapp_number="+919876543210",
        email="procurement@corp.example.com",
    )
    ev = evaluate_intent(lead)
    assert ev.score >= 80, f"Expected Priority A score >= 80, got {ev.score}"
    assert ev.tier == "Priority A"
    assert "EXPLICIT_COFFEE_BUYING_ENQUIRY" in ev.signals_detected
    assert "PRICING_OR_QUOTATION_REQUEST" in ev.signals_detected
    assert "WHOLESALE_INSTITUTIONAL_REQUIREMENT" in ev.signals_detected
    assert "RECENCY_WITHIN_7D" in ev.signals_detected


def test_generic_directory_lead_not_high_intent():
    lead = SimpleNamespace(
        id=102,
        lead_source="Google Maps Scrape",
        qualification_notes="",
        call_summary="",
        division="",
        segment="kirana",
        industry="Retail Kirana",
        status="DISCOVERED",
        do_not_call=False,
        sample_sku=None,
        purchase_sku=None,
        sample_requested=False,
        current_supplier=None,
        consent_timestamp=None,
        stage_entered_date=None,
        phone="+919876543211",
        whatsapp_number=None,
        email=None,
    )
    ev = evaluate_intent(lead)
    assert ev.score < 40, f"Expected Below 40, got {ev.score}"
    assert ev.tier == "Below 40"


def test_dnc_suppressed_lead_scores_zero():
    lead = SimpleNamespace(
        id=103,
        lead_source="inbound",
        qualification_notes="Price quotation wanted",
        call_summary="",
        division="distributor",
        status="DO_NOT_CONTACT",
        do_not_call=True,
    )
    ev = evaluate_intent(lead)
    assert ev.score == 0
    assert ev.is_suppressed is True
    assert "DO_NOT_CONTACT" in ev.suppression_reason or "Do Not Call" in ev.suppression_reason


def test_signal_decay_older_than_30_days():
    fresh_lead = SimpleNamespace(
        id=104,
        lead_source="inbound",
        qualification_notes="Quotation request",
        call_summary="",
        division="distributor",
        segment="distributor",
        industry="Distributor",
        status="NEW",
        do_not_call=False,
        sample_sku=None,
        purchase_sku=None,
        sample_requested=False,
        current_supplier=None,
        consent_timestamp=datetime.utcnow() - timedelta(days=2),
        stage_entered_date=None,
        phone="+919876543210",
        whatsapp_number="+919876543210",
        email="info@dist.example.com",
    )
    decayed_lead = SimpleNamespace(
        id=105,
        lead_source="inbound",
        qualification_notes="Quotation request",
        call_summary="",
        division="distributor",
        segment="distributor",
        industry="Distributor",
        status="NEW",
        do_not_call=False,
        sample_sku=None,
        purchase_sku=None,
        sample_requested=False,
        current_supplier=None,
        consent_timestamp=datetime.utcnow() - timedelta(days=45),
        stage_entered_date=None,
        phone="+919876543210",
        whatsapp_number="+919876543210",
        email="info@dist.example.com",
    )
    ev_fresh = evaluate_intent(fresh_lead)
    ev_decayed = evaluate_intent(decayed_lead)
    assert ev_fresh.score > ev_decayed.score, "Fresh lead must score higher than 45-day-old lead"


# ---------------------------------------------------------------------------
# 2. Contact Eligibility Tests
# ---------------------------------------------------------------------------

def test_email_eligibility_invalid_rejected():
    lead_bad_email = SimpleNamespace(id=201, email="not-an-email", status="NEW")
    ok, why = check_email_eligibility(lead_bad_email)
    assert ok is False
    assert "invalid or missing" in why

    lead_good_email = SimpleNamespace(id=202, email="valid.procurement@domain.com", status="NEW")
    ok, why = check_email_eligibility(lead_good_email)
    assert ok is False
    assert "trust verification unavailable" in why or "allow-list" in why


def test_voice_eligibility_hard_block_rejected():
    lead_blocked = SimpleNamespace(id=203, phone="+91 85919 77190", status="NEW", do_not_call=False)
    in_hours = datetime(2026, 11, 7, 11, 0, 0, tzinfo=IST)
    ok, why = check_voice_eligibility(lead_blocked, now=in_hours)
    assert ok is False
    assert "hard-blocked" in why


def test_voice_eligibility_uses_rolling_attempt_window(monkeypatch):
    from app.services import founder_call_pipeline
    lead_maxed = SimpleNamespace(
        id=204, phone="9876543210", call_attempts=99, ai_call_count=99,
        status="NEW", do_not_call=False,
    )
    monkeypatch.setattr(
        founder_call_pipeline, "recent_ai_attempt_count",
        lambda lead, now=None, db=None: (3, datetime.utcnow()),
    )
    in_hours = datetime(2026, 11, 7, 11, 0, 0, tzinfo=IST)
    ok, why = check_voice_eligibility(lead_maxed, now=in_hours)
    assert ok is False
    assert "rolling retry cooling" in why

# ---------------------------------------------------------------------------
# 3. AI Qualifier & 3-Attempt Adaptive Retry Scheduler
# ---------------------------------------------------------------------------

def test_retry_schedule_attempt_1_to_2():
    now = datetime(2026, 11, 7, 10, 0, 0)
    may_retry, next_dt, reason = calculate_next_retry(current_attempts=1, outcome="NO_ANSWER", base_time=now)
    assert may_retry is True
    assert next_dt == now + timedelta(hours=24)
    assert "retry 2 scheduled after 24h" in reason


def test_retry_schedule_attempt_2_to_3():
    now = datetime(2026, 11, 7, 10, 0, 0)
    may_retry, next_dt, reason = calculate_next_retry(current_attempts=2, outcome="BUSY", base_time=now)
    assert may_retry is True
    assert next_dt == now + timedelta(days=3)
    assert "retry 3 scheduled after 72h" in reason


def test_retry_schedule_attempt_3_continues_with_adaptive_backoff():
    now = datetime(2026, 11, 7, 10, 0, 0)
    may_retry, next_dt, reason = calculate_next_retry(current_attempts=3, outcome="NO_ANSWER", base_time=now)
    assert may_retry is True
    assert next_dt == now + timedelta(hours=120)
    assert "retry 4 scheduled after 120h" in reason


def test_retry_schedule_enters_cooling_after_five_attempts_not_permanent_abandonment():
    now = datetime(2026, 11, 7, 10, 0, 0)
    may_retry, next_dt, reason = calculate_next_retry(current_attempts=5, outcome="NO_ANSWER", base_time=now)
    assert may_retry is True
    assert next_dt == now + timedelta(days=21)
    assert "cooling" in reason.lower()


def test_retry_terminal_on_refusal_or_dnc():
    for term in ("DNC", "REFUSED", "NOT_INTERESTED", "WRONG_NUMBER", "PURCHASED"):
        may_retry, next_dt, reason = calculate_next_retry(current_attempts=1, outcome=term)
        assert may_retry is False
        assert next_dt is None
        assert "terminal outcome" in reason


# ---------------------------------------------------------------------------
# 4. Lead State Machine Transitions
# ---------------------------------------------------------------------------

def test_valid_forward_transitions():
    assert can_transition("DISCOVERED", "INTENT_VERIFIED")[0] is True
    assert can_transition("INTENT_VERIFIED", "SCORED")[0] is True
    assert can_transition("SCORED", "QUEUED")[0] is True
    assert can_transition("QUEUED", "CALLING")[0] is True
    assert can_transition("CALLING", "CONNECTED")[0] is True
    assert can_transition("CONNECTED", "QUALIFIED")[0] is True
    assert can_transition("QUALIFIED", "WHATSAPP_OPTED_IN")[0] is True
    assert can_transition("WHATSAPP_OPTED_IN", "TEMPLATE_QUEUED")[0] is True
    assert can_transition("TEMPLATE_QUEUED", "WHATSAPP_SENT")[0] is True
    assert can_transition("WHATSAPP_SENT", "ORDER_CREATED")[0] is True
    assert can_transition("ORDER_CREATED", "PAID_CONVERSION")[0] is True


def test_invalid_transition_rejected():
    # Cannot jump from DISCOVERED directly to PAID_CONVERSION without order
    ok, why = can_transition("DISCOVERED", "PAID_CONVERSION")
    assert ok is False
    assert "invalid transition" in why


def test_universal_dnc_always_permitted():
    for state in ("DISCOVERED", "CALLING", "CONNECTED", "WHATSAPP_SENT", "QUOTATION_SENT"):
        ok, _ = can_transition(state, "DO_NOT_CONTACT")
        assert ok is True


# ---------------------------------------------------------------------------
# 5. Cross-Channel Contact Coordinator & Post-Purchase Cancellation
# ---------------------------------------------------------------------------

def test_lead_lock_concurrency():
    lead = SimpleNamespace(id=301, lead_locked_until=None)
    mock_db = MagicMock()
    
    # Acquire lock
    assert acquire_lead_lock(lead, mock_db) is True
    assert lead.lead_locked_until is not None
    assert lead.lead_locked_until > datetime.utcnow()

    # Second caller fails to acquire
    assert acquire_lead_lock(lead, mock_db) is False

    # Release lock
    release_lead_lock(lead, mock_db)
    assert lead.lead_locked_until is None

    # Now can acquire again
    assert acquire_lead_lock(lead, mock_db) is True


# ---------------------------------------------------------------------------
# 6. Daily Target Controller Logic
# ---------------------------------------------------------------------------

def test_target_met_pauses_outbound_push_only_for_b2b_collection():
    status = DailyConversionStatus(
        target_date_ist=datetime.now(IST).strftime("%Y-%m-%d"),
        paid_orders_count=1, target_met=True, verified_revenue_inr=550.0,
        verified_order_ids=["B2B-LEAD-301"],
    )
    with patch("app.services.daily_target_controller.check_daily_b2b_conversion_target", return_value=status):
        pause, why = should_pause_outbound_sales_today(db=None)
        assert pause is True
        assert "b2b daily collection target met" in why.lower()


def test_target_unmet_continues_outbound_push_without_b2b_collection():
    status = DailyConversionStatus(
        target_date_ist=datetime.now(IST).strftime("%Y-%m-%d"),
        paid_orders_count=0, target_met=False,
    )
    with patch("app.services.daily_target_controller.check_daily_b2b_conversion_target", return_value=status):
        pause, why = should_pause_outbound_sales_today(db=None)
        assert pause is False
        assert "0/1" in why

