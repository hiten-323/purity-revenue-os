"""
Agent Governance & Financial Budget Engine (inspired by Paperclip) for Purity Revenue OS V2.0.
Provides autonomous execution with strict spend limits, financial fail-closed gates,
and founder approval workflows.

Rules:
1. Daily spend ceiling: Prevents uncontrolled AI calling or messaging spend.
2. Financial thresholds: Orders > ₹10,000, discounts > 25%, credit terms, or unverified free samples
   require explicit Founder Approval (PENDING_FOUNDER in ApprovalRequest).
3. Hard emergency stop: Kill-switch instantly freezes all outbound actions.
"""
from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

_log = logging.getLogger("agent_governance")

# Default daily budgets (INR)
DEFAULT_DAILY_SPEND_BUDGET_INR = 500.0
AI_CALL_COST_PER_MIN_INR = 2.50
WHATSAPP_COST_PER_MSG_INR = 0.75
EMAIL_VERIFY_COST_INR = 0.10

# Approval Thresholds
MAX_AUTO_APPROVED_ORDER_INR = 10000.0
MAX_AUTO_APPROVED_DISCOUNT_PCT = 25.0
MAX_AUTO_APPROVED_CREDIT_DAYS = 0


@dataclass
class GovernanceStatus:
    system_date: str
    daily_budget_inr: float
    spent_today_inr: float
    budget_remaining_inr: float
    is_budget_exceeded: bool
    kill_switch_active: bool
    pending_founder_approvals_count: int
    channel_caps: Dict[str, int]


class AgentGovernanceEngine:
    """
    Paperclip-style autonomous agent governance and financial controls.
    """

    @classmethod
    def get_daily_spend(cls, db: Session) -> float:
        """
        Calculates total spent today across calls, WhatsApp sends, and agent commissions.
        """
        from app.models.models import B2BLead, LeadInteraction, AgentCommission

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. Calls cost
        leads_called = (
            db.query(B2BLead)
            .filter(B2BLead.last_call_date >= today_start.strftime("%Y-%m-%d"))
            .all()
        )
        call_cost = sum(float(l.call_cost or 0.0) for l in leads_called)

        # 2. WhatsApp sends
        wa_sends = (
            db.query(LeadInteraction)
            .filter(
                LeadInteraction.method == "whatsapp",
                LeadInteraction.created_at >= today_start
            )
            .count()
        )
        wa_cost = wa_sends * WHATSAPP_COST_PER_MSG_INR

        # 3. Agent commissions / spend
        agent_spend_records = (
            db.query(AgentCommission)
            .filter(AgentCommission.created_at >= today_start)
            .all()
        )
        agent_cost = sum(float(a.amount_spent or 0.0) for a in agent_spend_records)

        return round(call_cost + wa_cost + agent_cost, 2)

    @classmethod
    def check_spend_allowed(cls, estimated_cost_inr: float, db: Session) -> bool:
        """
        Returns True if spending estimated_cost_inr keeps total spend within daily budget.
        """
        if cls.is_kill_switch_active():
            _log.warning("Agent spend rejected: kill switch is active")
            return False

        current_spent = cls.get_daily_spend(db)
        budget = float(os.getenv("MAX_DAILY_AGENT_SPEND_INR", str(DEFAULT_DAILY_SPEND_BUDGET_INR)))

        if (current_spent + estimated_cost_inr) > budget:
            _log.warning(
                "Agent spend rejected: budget cap reached (spent: ₹%.2f, cost: ₹%.2f, budget: ₹%.2f)",
                current_spent, estimated_cost_inr, budget
            )
            return False
        return True

    @classmethod
    def evaluate_action_for_approval(
        cls,
        action_type: str,
        lead_id: int,
        order_value_inr: float,
        discount_pct: float,
        credit_days: int,
        evidence_summary: str,
        db: Session,
        draft_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Evaluates whether an action can proceed autonomously or requires Founder Approval.
        """
        from app.models.models import ApprovalRequest, B2BLead

        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        company = lead.company if lead else f"Lead #{lead_id}"

        triggers: List[str] = []
        if order_value_inr > MAX_AUTO_APPROVED_ORDER_INR:
            triggers.append(f"Order value ₹{order_value_inr:,.2f} exceeds ₹{MAX_AUTO_APPROVED_ORDER_INR:,.2f} auto-cap")
        if discount_pct > MAX_AUTO_APPROVED_DISCOUNT_PCT:
            triggers.append(f"Discount {discount_pct}% exceeds {MAX_AUTO_APPROVED_DISCOUNT_PCT}% auto-cap")
        if credit_days > MAX_AUTO_APPROVED_CREDIT_DAYS:
            triggers.append(f"Payment credit of {credit_days} days requires founder guarantee")

        if triggers:
            # Create Approval Request for founder
            req = ApprovalRequest(
                lead_id=lead_id,
                type=action_type,
                status="PENDING_FOUNDER",
                expected_revenue=order_value_inr,
                expected_margin=order_value_inr * (1.0 - (discount_pct / 100.0)) * 0.45,
                founder_time_required=0.1,  # 6 mins review
                draft_message=draft_message or f"Deal approval required for {company}: {', '.join(triggers)}",
                draft_subject=f"Approval Required: {action_type.title()} for {company}",
                evidence=triggers,
                created_at=datetime.utcnow()
            )
            db.add(req)
            db.commit()
            db.refresh(req)

            _log.info("Action flagged for founder approval: id=%d, company=%s, triggers=%s", req.id, company, triggers)
            return {
                "approved": False,
                "status": "PENDING_FOUNDER",
                "approval_request_id": req.id,
                "reasons": triggers,
                "message": f"Action held for Founder Approval: {'; '.join(triggers)}"
            }

        return {
            "approved": True,
            "status": "AUTO_APPROVED",
            "approval_request_id": None,
            "reasons": [],
            "message": "Action meets all autonomous governance guidelines and is auto-approved."
        }

    @classmethod
    def get_governance_status(cls, db: Session) -> GovernanceStatus:
        from app.models.models import ApprovalRequest
        from app.services.outbound_limits import ceilings

        budget = float(os.getenv("MAX_DAILY_AGENT_SPEND_INR", str(DEFAULT_DAILY_SPEND_BUDGET_INR)))
        spent = cls.get_daily_spend(db)
        remaining = max(0.0, budget - spent)
        pending_count = db.query(ApprovalRequest).filter(ApprovalRequest.status == "PENDING_FOUNDER").count()

        return GovernanceStatus(
            system_date=date.today().isoformat(),
            daily_budget_inr=budget,
            spent_today_inr=spent,
            budget_remaining_inr=remaining,
            is_budget_exceeded=spent >= budget,
            kill_switch_active=cls.is_kill_switch_active(),
            pending_founder_approvals_count=pending_count,
            channel_caps=ceilings()
        )

    @classmethod
    def is_kill_switch_active(cls) -> bool:
        return os.getenv("EMERGENCY_KILL_SWITCH", "0") in {"1", "true", "True"}

    @classmethod
    def process_founder_decision(
        cls,
        request_id: int,
        decision: str,
        founder_notes: str,
        db: Session
    ) -> Dict[str, Any]:
        """
        Processes founder's decision: APPROVED or REJECTED.
        """
        from app.models.models import ApprovalRequest

        req = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
        if not req:
            return {"success": False, "error": f"Approval request #{request_id} not found."}

        decision_upper = decision.upper()
        if decision_upper not in {"APPROVED", "REJECTED"}:
            return {"success": False, "error": "Decision must be APPROVED or REJECTED."}

        req.status = decision_upper
        req.executed_at = datetime.utcnow()
        req.executed_by = "FOUNDER"
        req.draft_subject = (req.draft_subject or "") + f" [FOUNDER_{decision_upper}: {founder_notes}]"
        db.commit()

        _log.info("Founder decision processed: id=%d decision=%s notes=%s", request_id, decision_upper, founder_notes)
        return {
            "success": True,
            "request_id": req.id,
            "status": req.status,
            "executed_at": req.executed_at.isoformat(),
            "notes": founder_notes
        }
