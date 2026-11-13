"""
Daily High-Intent Conversion Report for Purity Revenue OS (Definite Answer Follow-Up Policy).

Directly answers the 11 essential executive questions from stored facts and
the founder-verified B2B payment ledger. Never reports SENT as DELIVERED or INTERESTED as CONVERTED.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any

from sqlalchemy import or_

from app.services.daily_target_controller import check_daily_b2b_conversion_target as check_daily_conversion_target, get_today_ist_boundaries
from app.services.high_intent_scorer import evaluate_intent

log = logging.getLogger("daily_conversion_report")
IST = ZoneInfo("Asia/Kolkata")

ACTIVE_CONTACT_STATUSES = ("CONTACTABLE", "NEEDS_RESEARCH")
SUPPRESSED_CONTACT_STATUSES = ("DO_NOT_CONTACT", "SUPPRESSED", "QUARANTINED")
TERMINAL_LEAD_STATUSES = (
    "DO_NOT_CONTACT", "OPTED_OUT", "WRONG_NUMBER", "CLOSED_UNRECOVERABLE",
    "PAID_CONVERSION", "DISQUALIFIED",
)
POSITIVE_COMMERCIAL_STATUSES = (
    "INBOUND_INTEREST", "QUALIFIED", "SAMPLE_REQUESTED", "SAMPLE_SENT",
    "QUOTATION_SENT", "PROPOSAL_SENT", "NEGOTIATION", "MEETING_BOOKED",
    "MEETING_COMPLETED",
)
QUOTE_OR_PROPOSAL_SENT_STATUSES = ("QUOTATION_SENT", "PROPOSAL_SENT")


def _not_voice_dnc(lead_model):
    return or_(lead_model.do_not_call.is_(False), lead_model.do_not_call.is_(None))


def generate_executive_daily_report(db) -> Dict[str, Any]:
    """
    Produces the comprehensive executive daily conversion report.
    """
    from app.models.models import B2BLead, WorkflowEvent, OutboundWhatsApp

    start_utc, end_utc = get_today_ist_boundaries()
    status = check_daily_conversion_target(db=db)

    # 1. Did we achieve one paid conversion?
    target_achieved = status.target_met

    # 2. How many high-intent leads discovered / active?
    all_leads = db.query(B2BLead).filter(
        ~B2BLead.status.in_(TERMINAL_LEAD_STATUSES),
        B2BLead.contact_status.in_(ACTIVE_CONTACT_STATUSES),
        _not_voice_dnc(B2BLead),
    ).all()
    high_intent_count = sum(1 for l in all_leads if evaluate_intent(l).score >= 60)

    # 3. How many were eligible to contact?
    from app.services.contact_eligibility import evaluate_lead_eligibility
    eligible_count = sum(1 for l in all_leads if evaluate_lead_eligibility(l, db=db).any_eligible)
    try:
        from app.services.ai_call_cycle import select_call_candidates
        voice_candidate_count = len(select_call_candidates(db, limit=100))
    except Exception:
        voice_candidate_count = None
    priced_b2b_proposals_count = db.query(B2BLead).filter(
        B2BLead.contact_status.in_(ACTIVE_CONTACT_STATUSES),
        _not_voice_dnc(B2BLead),
        B2BLead.proposal_suggested_price > 0,
        B2BLead.proposal_monthly_kg > 0,
    ).count()

    # 4. How many AI calls connected today?
    connected_calls = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "CALL_OUTCOME_RECORDED",
        WorkflowEvent.occurred_at >= start_utc,
        WorkflowEvent.occurred_at <= end_utc
    ).count()

    # 5. How many buyers qualified?
    qualified_today = db.query(B2BLead).filter(
        B2BLead.status == "QUALIFIED",
        B2BLead.stage_entered_date >= start_utc
    ).count()

    # 6. How many WhatsApp opt-ins were obtained?
    wa_optins_today = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type.in_(["WHATSAPP_CONSENT_RECORDED", "WHATSAPP_CONSENT_VERIFIED"]),
        WorkflowEvent.occurred_at >= start_utc,
        WorkflowEvent.occurred_at <= end_utc
    ).count()

    # 7. How many messages were actually delivered?
    # Distinguish queued vs delivered from provider
    queued_today = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "WHATSAPP_QUEUED_TO_KLAVIYO",
        WorkflowEvent.occurred_at >= start_utc,
        WorkflowEvent.occurred_at <= end_utc
    ).count()

    delivered_today = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "WHATSAPP_DELIVERED",
        WorkflowEvent.occurred_at >= start_utc,
        WorkflowEvent.occurred_at <= end_utc
    ).count()

    # 8. How many quotations and samples were requested?
    quotes_today = db.query(B2BLead).filter(
        B2BLead.status.in_(QUOTE_OR_PROPOSAL_SENT_STATUSES),
        B2BLead.stage_entered_date >= start_utc
    ).count()

    samples_today = db.query(B2BLead).filter(
        B2BLead.sample_requested == True,
        B2BLead.stage_entered_date >= start_utc
    ).count()

    # 9. How many paid orders were verified?
    paid_orders = status.paid_orders_count
    verified_revenue = status.verified_revenue_inr

    # 10. What is the biggest bottleneck?
    bottleneck = status.top_bottleneck
    if priced_b2b_proposals_count == 0:
        bottleneck = "B2B wholesale price, MOQ and priced proposal data are not configured"

    # 11. What should the system improve tomorrow?
    if target_achieved:
        improvement = "Maintain onboarding cadence and pursue secondary orders from engaged distributor partners."
    elif high_intent_count == 0:
        improvement = "Expand inbound intent discovery and qualify inbound leads with priority A signals."
    elif samples_today > 0 and paid_orders == 0:
        improvement = "Accelerate sample trial follow-up with factory pricing carton closes."
    elif priced_b2b_proposals_count == 0:
        improvement = (
            "Confirm B2B wholesale prices, MOQ, sample cost, GST and freight; "
            "then issue founder-approved quotes to actionable high-intent accounts."
        )
    else:
        improvement = "Progress verified, consented prospects through sample, quote, negotiation and payment follow-up."

    report = {
        "report_date_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "1_daily_target_achieved": target_achieved,
        "2_high_intent_leads_count": high_intent_count,
        "3_contact_eligible_leads": eligible_count,
        "3b_voice_eligible_candidates": voice_candidate_count,
        "3c_priced_b2b_proposals": priced_b2b_proposals_count,
        "4_connected_calls_today": connected_calls,
        "5_buyers_qualified_today": qualified_today,
        "6_whatsapp_optins_today": wa_optins_today,
        "7_whatsapp_queued_today": queued_today,
        "7b_whatsapp_delivered_today": delivered_today,
        "8_quotations_requested_today": quotes_today,
        "8b_samples_requested_today": samples_today,
        "9_verified_paid_orders": paid_orders,
        "9b_verified_revenue_inr": verified_revenue,
        "10_biggest_bottleneck": bottleneck,
        "11_tomorrow_improvement_focus": improvement,
    }
    return report


def generate_definite_answer_followup_report(db) -> Dict[str, Any]:
    """
    Directly answers the 11 executive questions specified in Section 12 of the Definite Answer Follow-Up Policy.
    """
    from app.models.models import B2BLead, ScheduledCallback, WorkflowEvent

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    status = check_daily_conversion_target(db=db)

    # 1. How many leads remain open?
    terminal_statuses = ["DO_NOT_CONTACT", "OPTED_OUT", "WRONG_NUMBER", "CLOSED_UNRECOVERABLE", "PAID_CONVERSION", "DISQUALIFIED"]
    open_leads = db.query(B2BLead).filter(
        ~B2BLead.status.in_(terminal_statuses),
        B2BLead.contact_status.in_(ACTIVE_CONTACT_STATUSES),
        _not_voice_dnc(B2BLead),
    ).count()

    # 2. How many have requested callbacks?
    callbacks_requested = db.query(ScheduledCallback).filter(
        ScheduledCallback.callback_status.in_(["CALLBACK_REQUESTED", "CALLBACK_SCHEDULED"])
    ).count()

    # 3. How many callbacks are due?
    callbacks_due = db.query(ScheduledCallback).filter(
        ScheduledCallback.callback_status.in_(["CALLBACK_SCHEDULED", "CALLBACK_DUE"]),
        ScheduledCallback.callback_window_start <= now_utc
    ).count()

    # 4. How many callbacks were completed?
    callbacks_completed = db.query(ScheduledCallback).filter(
        ScheduledCallback.callback_status == "CALLBACK_COMPLETED"
    ).count()

    # 5. How many were missed?
    callbacks_missed = db.query(ScheduledCallback).filter(
        ScheduledCallback.callback_status == "CALLBACK_MISSED"
    ).count()

    # 6. How many leads remain unanswered?
    unanswered_leads = db.query(B2BLead).filter(
        B2BLead.status.in_(["NO_ANSWER", "BUSY"]),
        B2BLead.call_attempts > 0
    ).count()

    # 7. How many are in cooling periods?
    cooling_leads = db.query(B2BLead).filter(
        B2BLead.status == "COOLING_PERIOD"
    ).count()

    # 8. How many gave a positive response?
    positive_leads = db.query(B2BLead.id).filter(
        B2BLead.contact_status.in_(ACTIVE_CONTACT_STATUSES),
        _not_voice_dnc(B2BLead),
        or_(
            B2BLead.status.in_(POSITIVE_COMMERCIAL_STATUSES),
            B2BLead.sample_requested.is_(True),
        )
    ).distinct().count()

    # 9. Count distinct accounts with a quote/proposal sent or a sample requested.
    quotes_and_samples = db.query(B2BLead.id).filter(
        B2BLead.contact_status.in_(ACTIVE_CONTACT_STATUSES),
        _not_voice_dnc(B2BLead),
        or_(
            B2BLead.status.in_(QUOTE_OR_PROPOSAL_SENT_STATUSES),
            B2BLead.sample_requested.is_(True),
        )
    ).distinct().count()

    # 10. Suppression is the union of global contact status and channel DNC.
    opted_out = db.query(B2BLead).filter(
        or_(
            B2BLead.contact_status.in_(SUPPRESSED_CONTACT_STATUSES),
            B2BLead.status.in_(["DO_NOT_CONTACT", "OPTED_OUT"]),
            B2BLead.do_not_call.is_(True),
        )
    ).count()

    # 11. How many were converted into paid orders?
    paid_conversions = status.paid_orders_count

    report = {
        "report_generated_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "1_open_leads_count": open_leads,
        "2_callbacks_requested_count": callbacks_requested,
        "3_callbacks_due_count": callbacks_due,
        "4_callbacks_completed_count": callbacks_completed,
        "5_callbacks_missed_count": callbacks_missed,
        "6_unanswered_leads_count": unanswered_leads,
        "7_cooling_period_leads_count": cooling_leads,
        "8_positive_response_count": positive_leads,
        "9_quotes_and_samples_count": quotes_and_samples,
        "10_opted_out_count": opted_out,
        "11_converted_paid_orders_count": paid_conversions,
    }
    return report


def print_daily_report(db) -> None:
    rep = generate_executive_daily_report(db)
    fol_rep = generate_definite_answer_followup_report(db)
    print("\n" + "=" * 70)
    print("      PURITY BEANS — HIGH-INTENT DAILY CONVERSION REPORT")
    print(f"      Generated At: {rep['report_date_ist']}")
    print("=" * 70)
    print(f"1. Target Achieved (>=1 Paid Order)? : {'✅ YES' if rep['1_daily_target_achieved'] else '❌ IN PROGRESS'}")
    print(f"2. Actionable High-Intent Leads      : {rep['2_high_intent_leads_count']}")
    print(f"3. Any-Channel Eligible Leads       : {rep['3_contact_eligible_leads']}")
    print(f"3b. Voice-Eligible Call Candidates  : {rep['3b_voice_eligible_candidates'] if rep['3b_voice_eligible_candidates'] is not None else 'UNKNOWN'}")
    print(f"3c. Priced B2B Proposals             : {rep['3c_priced_b2b_proposals']}")
    print(f"4. Connected Calls Today            : {rep['4_connected_calls_today']}")
    print(f"5. Buyers Qualified Today           : {rep['5_buyers_qualified_today']}")
    print(f"6. WhatsApp Opt-ins Today           : {rep['6_whatsapp_optins_today']}")
    print(f"7. WhatsApp Queued / Delivered      : {rep['7_whatsapp_queued_today']} queued / {rep['7b_whatsapp_delivered_today']} confirmed delivered")
    print(f"8. Quotes/Proposals Sent / Samples   : {rep['8_quotations_requested_today']} quotes / {rep['8b_samples_requested_today']} samples")
    print(f"9. Verified B2B Customer Collections   : {rep['9_verified_paid_orders']} orders (₹{rep['9b_verified_revenue_inr']:.2f})")
    print(f"10. Biggest Operational Bottleneck  : {rep['10_biggest_bottleneck']}")
    print(f"11. Recommended Focus Tomorrow      : {rep['11_tomorrow_improvement_focus']}")
    print("=" * 70)
    print("      DEFINITE ANSWER FOLLOW-UP POLICY METRICS (§12)")
    print("=" * 70)
    print(f"1. Open Non-Suppressed B2B Leads     : {fol_rep['1_open_leads_count']}")
    print(f"2. Callbacks Requested              : {fol_rep['2_callbacks_requested_count']}")
    print(f"3. Callbacks Due Now                : {fol_rep['3_callbacks_due_count']}")
    print(f"4. Callbacks Completed              : {fol_rep['4_callbacks_completed_count']}")
    print(f"5. Callbacks Missed                 : {fol_rep['5_callbacks_missed_count']}")
    print(f"6. Leads Remaining Unanswered       : {fol_rep['6_unanswered_leads_count']}")
    print(f"7. Leads in Cooling Periods         : {fol_rep['7_cooling_period_leads_count']}")
    print(f"8. Actionable Commercially Engaged Leads: {fol_rep['8_positive_response_count']}")
    print(f"9. Actionable Quotes/Proposals & Samples: {fol_rep['9_quotes_and_samples_count']}")
    print(f"10. DNC / Suppressed Records          : {fol_rep['10_opted_out_count']}")
    print(f"11. Converted into Paid Orders      : {fol_rep['11_converted_paid_orders_count']}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    from app.database.database import SessionLocal
    db = SessionLocal()
    try:
        print_daily_report(db)
    finally:
        db.close()
