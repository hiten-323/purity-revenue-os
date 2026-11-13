"""
Daily One-Paid-Conversion Controller for Purity Revenue OS.

Business Objective: At least ONE genuine, verified, paid customer conversion every day.

Integrates with Shopify API:
- Queries orders for the current business day (Asia/Kolkata timezone: 00:00 to 23:59 IST).
- Strictly counts only verified paid orders: financial_status == 'paid',
  cancelled_at is None, test is False.
- Never counts calls, opt-ins, quotations, sample requests, or unpaid COD as conversions.
- Automatically halts aggressive automated outbound push once the daily target is achieved.
- Analyzes bottlenecks when target is not yet met.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import httpx

log = logging.getLogger("daily_target_controller")
IST = ZoneInfo("Asia/Kolkata")


@dataclass
class DailyConversionStatus:
    target_date_ist: str
    target_units: int = 1
    paid_orders_count: int = 0
    target_met: bool = False
    verified_revenue_inr: float = 0.0
    verified_order_ids: List[str] = field(default_factory=list)
    top_bottleneck: str = ""
    bottleneck_details: Dict[str, Any] = field(default_factory=dict)
    high_intent_queue_count: int = 0
    checked_at: str = field(default_factory=lambda: datetime.now(IST).isoformat())


def get_today_ist_boundaries() -> tuple[datetime, datetime]:
    """Returns (start_of_day_utc, end_of_day_utc) for today in Asia/Kolkata timezone."""
    now_ist = datetime.now(IST)
    start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    end_ist = now_ist.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_utc = start_ist.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_utc = end_ist.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return start_utc, end_utc


def fetch_shopify_paid_orders_today(timeout: float = 12.0) -> tuple[int, float, List[dict]]:
    """
    Directly queries Shopify Admin API for orders placed today (IST),
    filtering strictly for paid, non-cancelled, non-test orders.
    """
    store = (os.getenv("SHOPIFY_STORE", "") or "").strip()
    token = (os.getenv("SHOPIFY_TOKEN", "") or "").strip()
    if not store or not token:
        try:
            from dotenv import dotenv_values
            from pathlib import Path
            env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
            store = (env.get("SHOPIFY_STORE") or "").strip()
            token = (env.get("SHOPIFY_TOKEN") or "").strip()
        except Exception:
            pass

    if not store or not token:
        log.warning("Shopify credentials not configured; cannot verify paid orders")
        return 0, 0.0, []

    now_ist = datetime.now(IST)
    start_of_day_ist_iso = now_ist.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    url = f"https://{store}/admin/api/2024-01/orders.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    params = {
        "status": "any",
        "created_at_min": start_of_day_ist_iso,
        "limit": 50,
    }

    try:
        r = httpx.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code != 200:
            log.warning("Shopify orders API returned HTTP %s", r.status_code)
            return 0, 0.0, []

        data = r.json().get("orders", [])
        verified_orders = []
        total_revenue = 0.0

        for o in data:
            # Strict verification criteria
            if o.get("test") is True:
                continue
            if o.get("cancelled_at") is not None:
                continue
            fin_status = (o.get("financial_status") or "").lower()
            if fin_status != "paid":
                continue

            price = float(o.get("total_price") or 0.0)
            total_revenue += price
            verified_orders.append(o)

        return len(verified_orders), total_revenue, verified_orders

    except Exception as exc:
        log.warning("Shopify connection error during order fetch: %s", exc)
        return 0, 0.0, []


def diagnose_conversion_bottlenecks(db) -> tuple[str, dict]:
    """
    Examines database state to determine why the daily conversion has not occurred yet.
    """
    if db is None:
        return "Database unavailable for bottleneck diagnosis", {}

    from app.models.models import B2BLead, WorkflowEvent, EmailDraft

    # 1. Check for pending callbacks
    callbacks = db.query(B2BLead).filter(
        B2BLead.status.in_(["MEETING_BOOKED", "CALLBACK"]),
        ~B2BLead.status.in_(["DO_NOT_CONTACT", "CLOSED_LOST"])
    ).count()

    # 2. Check for sample requests awaiting dispatch
    samples = db.query(B2BLead).filter(
        B2BLead.sample_requested == True,
        B2BLead.status != "ORDER_WON"
    ).count()

    # 3. Check for high-intent leads ready for contact
    from app.services.high_intent_scorer import evaluate_intent
    active_leads = db.query(B2BLead).filter(
        ~B2BLead.status.in_(["DO_NOT_CONTACT", "CLOSED_UNRECOVERABLE", "WRONG_NUMBER"])
    ).limit(100).all()

    priority_a = sum(1 for l in active_leads if evaluate_intent(l).tier == "Priority A")
    priority_b = sum(1 for l in active_leads if evaluate_intent(l).tier == "Priority B")

    details = {
        "pending_callbacks": callbacks,
        "sample_requests_active": samples,
        "priority_a_leads": priority_a,
        "priority_b_leads": priority_b,
    }

    if samples > 0:
        bottleneck = f"{samples} sample requests waiting for commercial follow-up"
    elif callbacks > 0:
        bottleneck = f"{callbacks} scheduled callbacks pending founder engagement"
    elif priority_a == 0 and priority_b == 0:
        bottleneck = "0 high-intent Priority A/B leads available in active cohort"
    else:
        bottleneck = "High-intent leads queued; awaiting response / commercial negotiation"

    return bottleneck, details


def check_daily_conversion_target(db=None) -> DailyConversionStatus:
    """
    The master daily conversion target evaluator.
    Answers: Did we achieve at least one verified paid conversion today?
    """
    paid_count, total_revenue, orders = fetch_shopify_paid_orders_today()
    target_met = paid_count >= 1
    order_ids = [str(o.get("id")) for o in orders]

    bottleneck = ""
    details = {}
    if not target_met:
        bottleneck, details = diagnose_conversion_bottlenecks(db)

    return DailyConversionStatus(
        target_date_ist=datetime.now(IST).strftime("%Y-%m-%d"),
        target_units=1,
        paid_orders_count=paid_count,
        target_met=target_met,
        verified_revenue_inr=total_revenue,
        verified_order_ids=order_ids,
        top_bottleneck=bottleneck,
        bottleneck_details=details,
        high_intent_queue_count=details.get("priority_a_leads", 0) + details.get("priority_b_leads", 0),
    )


def check_daily_b2b_conversion_target(db=None) -> DailyConversionStatus:
    """Count unique B2B customers with founder-verified cash in the B2B ledger.

    Workflow events are useful audit evidence, but the unique B2BPayment ledger
    is the source of truth for collections. Shopify D2C orders are excluded.
    Multiple partial payments for one account count as one customer conversion.
    """
    if db is None:
        return DailyConversionStatus(
            target_date_ist=datetime.now(IST).strftime('%Y-%m-%d'),
            top_bottleneck='B2B database unavailable',
        )
    from app.models.models import B2BPayment
    start_utc, end_utc = get_today_ist_boundaries()
    payments = db.query(B2BPayment).filter(
        B2BPayment.status == 'FOUNDER_VERIFIED',
        B2BPayment.recorded_by == 'FOUNDER',
        B2BPayment.currency == 'INR',
        B2BPayment.amount_inr > 0,
        B2BPayment.recorded_at >= start_utc,
        B2BPayment.recorded_at <= end_utc,
    ).all()
    valid = [
        payment for payment in payments
        if payment.lead_id and float(payment.amount_inr or 0) > 0
    ]
    lead_ids = sorted({payment.lead_id for payment in valid})
    met = bool(lead_ids)
    return DailyConversionStatus(
        target_date_ist=datetime.now(IST).strftime('%Y-%m-%d'),
        target_units=1,
        paid_orders_count=len(lead_ids),
        target_met=met,
        verified_revenue_inr=sum(float(payment.amount_inr) for payment in valid),
        verified_order_ids=[f'B2B-LEAD-{lead_id}' for lead_id in lead_ids],
        top_bottleneck='' if met else 'No founder-verified B2B collections today',
    )


def should_pause_outbound_sales_today(db=None) -> tuple[bool, str]:
    """Pause B2B outbound only after a verified B2B collection today."""
    status = check_daily_b2b_conversion_target(db=db)
    if status.target_met:
        return True, f"B2B daily collection target met ({status.paid_orders_count} verified account(s) today)"
    return False, f"B2B target in progress: {status.paid_orders_count}/1 verified collections"


def reconcile_shopify_paid_orders(db) -> dict:
    """Retired: Shopify D2C orders must never mutate B2B leads or revenue."""
    return {
        "status": "excluded_from_b2b",
        "reason": "Shopify D2C orders are not B2B conversions or B2B revenue",
        "paid_orders": 0,
        "matched_leads": 0,
        "new_customers": 0,
        "revenue_inr": 0.0,
    }
