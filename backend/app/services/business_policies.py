"""
Business Policies — Layer 4 of the Purity Beans Founder Revenue OS V1.1

All eligibility rules, limits, and approval requirements live here.
Services and workflows call these before acting. No policy logic in UI.
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.models import B2BLead


class CallingPolicy:
    MAX_ATTEMPTS = 5
    MIN_HOURS_BETWEEN_CALLS = 24
    CALL_WINDOW_START = 9   # 9 AM
    CALL_WINDOW_END = 19    # 7 PM

    @classmethod
    def can_call(cls, lead: "B2BLead") -> tuple[bool, str]:
        if lead.do_not_call:
            return False, f"DNC: {lead.dnc_reason or 'opted out'}"
        if (lead.call_attempts or 0) >= cls.MAX_ATTEMPTS:
            return False, f"Max {cls.MAX_ATTEMPTS} call attempts reached"
        if lead.last_call_date:
            hours_since = (datetime.utcnow() - lead.last_call_date).total_seconds() / 3600
            if hours_since < cls.MIN_HOURS_BETWEEN_CALLS:
                return False, f"Wait {int(cls.MIN_HOURS_BETWEEN_CALLS - hours_since)}h before next call"
        hour = datetime.now().hour
        if not (cls.CALL_WINDOW_START <= hour < cls.CALL_WINDOW_END):
            return False, f"Outside call window ({cls.CALL_WINDOW_START}–{cls.CALL_WINDOW_END})"
        return True, "ok"

    @classmethod
    def consent_status(cls, lead: "B2BLead") -> str:
        return lead.consent_status or "UNKNOWN"


class SamplePolicy:
    MAX_SAMPLE_VALUE_RS = 2000
    REQUIRE_APPROVAL_ABOVE_RS = 500
    ELIGIBLE_STATUSES = [
        "MEETING_COMPLETED", "REPLIED", "FOUNDER_CALLED",
        "AI_CALLED", "PROPOSAL_SENT",
    ]

    @classmethod
    def can_dispatch(cls, lead: "B2BLead") -> tuple[bool, str]:
        if lead.status not in cls.ELIGIBLE_STATUSES:
            return False, f"Lead must be in {cls.ELIGIBLE_STATUSES} to receive sample"
        value = (lead.sampling_cost_total or 0)
        if value >= cls.MAX_SAMPLE_VALUE_RS:
            return False, f"Sampling cost Rs.{value} exceeds limit Rs.{cls.MAX_SAMPLE_VALUE_RS}"
        return True, "ok"

    @classmethod
    def requires_approval(cls, estimated_sample_cost: float) -> bool:
        return estimated_sample_cost > cls.REQUIRE_APPROVAL_ABOVE_RS


class DiscountPolicy:
    MAX_DISCOUNT_PCT = 15.0
    REQUIRE_FOUNDER_APPROVAL_ABOVE_PCT = 8.0
    VOLUME_TIERS = [
        (500, 5.0),   # >= 500 kg/month → 5%
        (200, 3.0),   # >= 200 kg/month → 3%
        (100, 1.5),
        (0,   0.0),
    ]

    @classmethod
    def max_allowed(cls, lead: "B2BLead") -> float:
        return cls.MAX_DISCOUNT_PCT

    @classmethod
    def volume_discount(cls, monthly_kg: float) -> float:
        for threshold, pct in cls.VOLUME_TIERS:
            if monthly_kg >= threshold:
                return pct
        return 0.0

    @classmethod
    def requires_approval(cls, discount_pct: float) -> bool:
        return discount_pct > cls.REQUIRE_FOUNDER_APPROVAL_ABOVE_PCT

    @classmethod
    def validate(cls, discount_pct: float) -> tuple[bool, str]:
        if discount_pct > cls.MAX_DISCOUNT_PCT:
            return False, f"Discount {discount_pct}% exceeds max {cls.MAX_DISCOUNT_PCT}%"
        return True, "ok"


class MarginPolicy:
    MIN_GROSS_MARGIN_PCT = 28.0
    TARGET_MARGIN_PCT = 31.0
    BLENDED_REALIZATION_PER_KG = 1400.0

    @classmethod
    def gross_margin(cls, selling_price_per_kg: float, cost_per_kg: float = 968.0) -> float:
        if selling_price_per_kg <= 0:
            return 0.0
        return ((selling_price_per_kg - cost_per_kg) / selling_price_per_kg) * 100

    @classmethod
    def is_acceptable(cls, margin_pct: float) -> bool:
        return margin_pct >= cls.MIN_GROSS_MARGIN_PCT

    @classmethod
    def estimated_margin_rs(cls, estimated_value: float) -> float:
        return estimated_value * (cls.TARGET_MARGIN_PCT / 100)


class ReorderPolicy:
    REORDER_TRIGGER_DAYS = 25    # predict reorder 25 days before stock-out
    MIN_ORDER_KG = 20
    AUTO_TRIGGER_ABOVE_KG = 100  # auto-generate reorder alert for high-volume accounts

    @classmethod
    def should_trigger(cls, lead: "B2BLead") -> bool:
        if lead.status != "ORDER_WON":
            return False
        monthly_kg = lead.expected_monthly_consumption_kg or 0
        return monthly_kg >= cls.MIN_ORDER_KG

    @classmethod
    def days_until_reorder(cls, lead: "B2BLead") -> int:
        if not lead.stage_entered_date:
            return 30
        monthly_kg = lead.expected_monthly_consumption_kg or 50
        days_per_kg_batch = 30 / max(1, monthly_kg / 20)
        delta = (lead.stage_entered_date + timedelta(days=int(days_per_kg_batch))) - datetime.utcnow()
        return max(0, delta.days)


class ProposalPolicy:
    ELIGIBLE_STATUSES = [
        "MEETING_COMPLETED", "SAMPLE_SENT", "REPLIED", "FOUNDER_CALLED",
    ]
    MAX_CREDIT_DAYS = 45
    DEFAULT_CREDIT_DAYS = 30

    @classmethod
    def can_send(cls, lead: "B2BLead") -> tuple[bool, str]:
        if lead.status not in cls.ELIGIBLE_STATUSES:
            return False, f"Lead must be in {cls.ELIGIBLE_STATUSES}"
        if not lead.proposal_monthly_kg or lead.proposal_monthly_kg <= 0:
            return False, "Monthly kg quantity required before sending proposal"
        return True, "ok"

    @classmethod
    def validate_credit_days(cls, days: int) -> tuple[bool, str]:
        if days > cls.MAX_CREDIT_DAYS:
            return False, f"Credit period {days} days exceeds max {cls.MAX_CREDIT_DAYS}"
        return True, "ok"
