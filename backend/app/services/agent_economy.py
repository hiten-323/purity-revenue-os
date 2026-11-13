from datetime import datetime
from math import isfinite
from sqlalchemy.orm import Session
from app.models.models import AIAgentPerformance, AIGrowthTreasurySettings, SubscriptionProposal, LeadAttributionPath, B2BLead, Product, EmailDraft, B2BPayment

AGENTS_SEED = [
    {"id": "discovery", "name": "Discovery Agent", "objective": "Find real businesses with observable coffee-buying/reselling potential.", "confidence": "HIGH"},
    {"id": "qualification", "name": "Qualification Agent", "objective": "Reject irrelevant businesses and rank genuine opportunities.", "confidence": "HIGH"},
    {"id": "contact_intelligence", "name": "Contact Intelligence Agent", "objective": "Find and verify legitimate contact methods.", "confidence": "MEDIUM"},
    {"id": "email_sales", "name": "Email Sales Agent", "objective": "Generate category-specific high-conversion outreach.", "confidence": "HIGH"},
    {"id": "followup", "name": "Follow-Up Agent", "objective": "Move non-responsive opportunities forward without spamming.", "confidence": "MEDIUM"},
    {"id": "reply_intelligence", "name": "Reply Intelligence Agent", "objective": "Interpret genuine responses and determine the next action.", "confidence": "MEDIUM"},
    {"id": "founder_call_coach", "name": "Founder Call Coach", "objective": "Turn high-intent opportunities into founder conversations.", "confidence": "HIGH"},
    {"id": "sample_conversion", "name": "Sample Conversion Agent", "objective": "Convert interested prospects into sample trials.", "confidence": "MEDIUM"},
    {"id": "proposal", "name": "Proposal Agent", "objective": "Convert qualified demand into commercial proposals.", "confidence": "HIGH"},
    {"id": "closing", "name": "Closing Agent", "objective": "Identify stalled deals, objections and next-best actions.", "confidence": "MEDIUM"},
    {"id": "reorder", "name": "Reorder Agent", "objective": "Generate repeat orders from won customers.", "confidence": "LOW"},
    {"id": "government_tender", "name": "Government/Tender Agent", "objective": "Find genuine relevant opportunities and prepare compliant drafts without falsely claiming submission.", "confidence": "LOW"}
]

PROPOSALS_SEED = [
    {
        "tool_name": "Premium Lead Finder (Google Maps Search API)",
        "agent_id": "discovery",
        "bottleneck_removed": "Low discovery limits restricts lead prospecting volume",
        "monthly_cost": 2500.0,
        "projected_uplift": 12000.0,
        "payback_period_months": 0.21,
        "confidence": "MEDIUM"
    },
    {
        "tool_name": "WhatsApp Bulk API (Official WhatsApp Platform)",
        "agent_id": "email_sales",
        "bottleneck_removed": "Low WhatsApp message sending limit slows conversion velocity",
        "monthly_cost": 3500.0,
        "projected_uplift": 18000.0,
        "payback_period_months": 0.19,
        "confidence": "HIGH"
    },
    {
        "tool_name": "Custom Objection Finetuned LLM",
        "agent_id": "closing",
        "bottleneck_removed": "Stalled objections block sample and proposal conversions",
        "monthly_cost": 2000.0,
        "projected_uplift": 8000.0,
        "payback_period_months": 0.25,
        "confidence": "LOW"
    },
    {
        "tool_name": "SMS Fallback Gateway (Twilio credits)",
        "agent_id": "followup",
        "bottleneck_removed": "Non-responsive opportunities go uncontacted on bounce",
        "monthly_cost": 1000.0,
        "projected_uplift": 4000.0,
        "payback_period_months": 0.25,
        "confidence": "MEDIUM"
    }
]

ATTRIBUTION_SPLITS = {
    "discovery": 0.15,
    "qualification": 0.10,
    "contact_intelligence": 0.10,
    "email_sales": 0.15,
    "followup": 0.10,
    "reply_intelligence": 0.10,
    "founder_call_coach": 0.10,
    "sample_conversion": 0.05,
    "proposal": 0.05,
    "closing": 0.05,
    "reorder": 0.03,
    "government_tender": 0.02
}

class AgentEconomyService:
    @staticmethod
    def seed_if_empty(db: Session):
        # Settings
        settings = db.query(AIGrowthTreasurySettings).first()
        if not settings:
            settings = AIGrowthTreasurySettings(
                reinvestment_rate=0.0,
                reinvestment_enabled=False,
                bootstrap_mode=True,
                realised_revenue=0.0,
                realised_margin=0.0,
                ai_reinvestment_reserve=0.0
            )
            db.add(settings)
            db.commit()

        # Agents
        agent_count = db.query(AIAgentPerformance).count()
        if agent_count == 0:
            for item in AGENTS_SEED:
                agent = AIAgentPerformance(
                    agent_id=item["id"],
                    agent_name=item["name"],
                    objective=item["objective"],
                    attribution_confidence=item["confidence"]
                )
                db.add(agent)
            db.commit()

        # Proposals
        prop_count = db.query(SubscriptionProposal).count()
        if prop_count == 0:
            for item in PROPOSALS_SEED:
                prop = SubscriptionProposal(
                    tool_name=item["tool_name"],
                    agent_id=item["agent_id"],
                    bottleneck_removed=item["bottleneck_removed"],
                    monthly_cost=item["monthly_cost"],
                    projected_uplift=item["projected_uplift"],
                    payback_period_months=item["payback_period_months"],
                    confidence=item["confidence"],
                    justification_status="NOT JUSTIFIED",
                    status="PROPOSED"
                )
                db.add(prop)
            db.commit()

    @staticmethod
    def get_treasury(db: Session) -> dict:
        AgentEconomyService.seed_if_empty(db)
        settings = db.query(AIGrowthTreasurySettings).first()
        active_cost = db.query(SubscriptionProposal).filter(
            SubscriptionProposal.status == "ACTIVE"
        ).all()
        current_software_cost = sum(p.monthly_cost for p in active_cost)

        payments = db.query(B2BPayment).filter(
            B2BPayment.status == "FOUNDER_VERIFIED"
        ).all()
        verified_revenue = sum(float(p.amount_inr or 0) for p in payments)
        verified_accounts = len({p.lead_id for p in payments})

        # There is not yet an invoice-line ledger tying B2B selling price,
        # SKU quantity, landed COGS, freight and credit notes together.
        # Product.selling_price is a channel/list price, not B2B invoice proof.
        # Therefore margin and any reserve derived from it remain frozen.
        return {
            "realised_revenue": verified_revenue,
            "revenue_verification_status": "VERIFIED_PAYMENT_LEDGER" if payments else "NO_VERIFIED_B2B_PAYMENT_RECORDS",
            "verified_payment_count": len(payments),
            "verified_b2b_accounts_count": verified_accounts,
            "unverified_legacy_revenue": settings.realised_revenue,
            "realised_margin": "PENDING_B2B_INVOICE_COSTING",
            "margin_verification_status": "PENDING_INVOICE_LINE_COGS_AND_CREDIT_NOTES",
            "ai_reinvestment_reserve": "FROZEN_UNVERIFIED_MARGIN",
            "unverified_legacy_reserve": settings.ai_reinvestment_reserve,
            "current_software_cost": current_software_cost,
            "available_for_reinvestment": 0.0,
            "reinvestment_rate": settings.reinvestment_rate,
            "reinvestment_enabled": settings.reinvestment_enabled,
            "bootstrap_mode": settings.bootstrap_mode,
        }

    @staticmethod
    def update_treasury_settings(db: Session, rate: float, enabled: bool, bootstrap: bool):
        AgentEconomyService.seed_if_empty(db)
        settings = db.query(AIGrowthTreasurySettings).first()
        settings.reinvestment_rate = rate
        settings.reinvestment_enabled = enabled
        settings.bootstrap_mode = bootstrap
        db.commit()

    @staticmethod
    def get_agents(db: Session) -> list:
        AgentEconomyService.seed_if_empty(db)
        agents = db.query(AIAgentPerformance).all()
        payments = db.query(B2BPayment).filter(B2BPayment.status == "FOUNDER_VERIFIED").all()
        amounts = {p.payment_reference: float(p.amount_inr or 0) for p in payments}
        rows = db.query(LeadAttributionPath).filter(
            LeadAttributionPath.stage == "PAYMENT_VERIFIED",
            LeadAttributionPath.payment_reference.isnot(None),
        ).all()
        by_agent = {}
        leads_by_agent = {}
        for row in rows:
            amount = amounts.get(row.payment_reference)
            if amount is None:
                continue
            by_agent[row.agent_id] = by_agent.get(row.agent_id, 0.0) + amount * float(row.weight or 0)
            leads_by_agent.setdefault(row.agent_id, set()).add(row.lead_id)

        return [{
            "agent_id": a.agent_id,
            "agent_name": a.agent_name,
            "objective": a.objective,
            "actions_completed": a.actions_completed,
            "opportunities_influenced": a.opportunities_influenced,
            "qualified_replies": a.qualified_replies,
            "meetings_generated": a.meetings_generated,
            "samples_generated": a.samples_generated,
            "proposals_generated": a.proposals_generated,
            "orders_influenced": len(leads_by_agent.get(a.agent_id, set())),
            "realised_revenue": round(by_agent.get(a.agent_id, 0.0), 4),
            "legacy_unverified_revenue": a.realised_revenue,
            "realised_margin": 0.0,
            "realised_margin_status": "PENDING_B2B_INVOICE_COSTING",
            "founder_minutes_used": a.founder_minutes_used,
            "cost": a.cost,
            "roi": 0.0,
            "roi_status": "PENDING_B2B_INVOICE_COSTING",
            "attribution_confidence": a.attribution_confidence,
        } for a in agents]

    @staticmethod
    def get_proposals(db: Session) -> list:
        AgentEconomyService.seed_if_empty(db)
        AgentEconomyService.evaluate_upgrade_proposals(db)
        return db.query(SubscriptionProposal).all()

    @staticmethod
    def update_proposal_status(db: Session, proposal_id: int, status: str) -> bool:
        prop = db.query(SubscriptionProposal).filter(SubscriptionProposal.id == proposal_id).first()
        if not prop:
            return False
        
        prop.status = status
        if status == "APPROVED":
            prop.approved_at = datetime.utcnow()
        db.commit()
        return True

    @staticmethod
    def verify_payment_and_allocate(
        db: Session,
        lead_id: int,
        actual_cash: float,
        payment_reference: str,
        before_status: str | None = None,
    ) -> dict:
        AgentEconomyService.seed_if_empty(db)
        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            return {"status": "error", "message": "Lead not found"}
        reference = (payment_reference or "").strip().upper()
        if len(reference) < 4:
            return {"status": "error", "message": "A valid payment reference is required"}
        existing = db.query(B2BPayment).filter(
            B2BPayment.payment_reference == reference
        ).first()
        if existing:
            return {"status": "error", "message": "This payment reference has already been recorded"}

        amount = float(actual_cash)
        if not isfinite(amount) or amount <= 0:
            return {"status": "error", "message": "Actual cash must be a positive finite amount"}

        prior_payments = db.query(B2BPayment).filter(B2BPayment.lead_id == lead_id).count()
        payment_row = B2BPayment(
            lead_id=lead_id,
            payment_reference=reference,
            amount_inr=amount,
            currency="INR",
            status="FOUNDER_VERIFIED",
            recorded_by="FOUNDER",
        )
        db.add(payment_row)
        db.flush()

        # D2C Product.selling_price and SKU-level cost are not B2B invoice
        # economics. Do not infer margin or release reserve until invoice lines
        # carry negotiated B2B unit price, quantity, landed COGS and adjustments.
        cogs_known = False
        margin = 0.0
        reserve_added = 0.0

        path_status = {
            "discovery": True,
            "qualification": bool((lead.score and lead.score > 0) or lead.qualification_notes),
            "contact_intelligence": bool(lead.email_verified or lead.phone_verified or lead.contact_searched_at),
            "email_sales": False,
            "followup": bool(lead.email_sequence_stage and lead.email_sequence_stage > 1),
            "reply_intelligence": bool(lead.status == "REPLIED" or (lead.email_opens and lead.email_opens > 0)),
            "founder_call_coach": bool((lead.call_attempts and lead.call_attempts > 0) or (lead.call_duration_seconds and lead.call_duration_seconds > 0)),
            "sample_conversion": bool(lead.sample_requested or lead.sample_sku),
            "proposal": bool(lead.proposal_suggested_price or lead.proposal_monthly_kg or lead.proposal_text),
            "closing": bool(lead.status in ("ORDER_WON", "PAYMENT_VERIFIED")),
            "reorder": bool(lead.reorder_sku),
            "government_tender": bool((lead.division and lead.division.lower() == "govt_canteen") or (lead.industry and lead.industry.lower() == "government")),
        }
        draft_count = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id).count()
        if draft_count > 0 or (lead.email_sequence_stage and lead.email_sequence_stage > 0):
            path_status["email_sales"] = True

        active_weight_sum = sum(
            ATTRIBUTION_SPLITS[agent_id]
            for agent_id, active in path_status.items() if active
        ) or 1.0

        for agent_id, default_weight in ATTRIBUTION_SPLITS.items():
            if not path_status.get(agent_id, False):
                continue
            split_weight = default_weight / active_weight_sum
            db.add(LeadAttributionPath(
                lead_id=lead_id,
                stage="PAYMENT_VERIFIED",
                agent_id=agent_id,
                weight=split_weight,
                occurred_at=datetime.utcnow(),
                payment_reference=reference,
            ))

        from app.services.pipeline_tracker import track
        track(
            db,
            "PAYMENT_VERIFIED",
            lead_id=lead_id,
            actor="FOUNDER",
            channel="founder",
            before_status=before_status,
            after_status=lead.status,
            payload={
                "payment_id": payment_row.id,
                "payment_reference": reference,
                "actual_cash": amount,
                "currency": "INR",
                "margin_status": "PENDING_B2B_INVOICE_COSTING",
            },
        )
        db.commit()
        AgentEconomyService.evaluate_upgrade_proposals(db)

        return {
            "status": "success",
            "payment_id": payment_row.id,
            "payment_reference": reference,
            "realised_revenue": amount,
            "realised_margin": "PENDING_B2B_INVOICE_COSTING",
            "reserve_added": reserve_added,
            "cogs_known": cogs_known,
            "prior_payments_for_account": prior_payments,
        }

    @staticmethod
    def increment_agent_actions(db: Session, agent_id: str, action_type: str = "completed"):
        AgentEconomyService.seed_if_empty(db)
        agent = db.query(AIAgentPerformance).filter(AIAgentPerformance.agent_id == agent_id).first()
        if agent:
            if action_type == "completed":
                agent.actions_completed += 1
            elif action_type == "influenced":
                agent.opportunities_influenced += 1
            elif action_type == "replies":
                agent.qualified_replies += 1
            elif action_type == "meetings":
                agent.meetings_generated += 1
            elif action_type == "samples":
                agent.samples_generated += 1
            elif action_type == "proposals":
                agent.proposals_generated += 1
            db.commit()

    @staticmethod
    def evaluate_upgrade_proposals(db: Session):
        settings = db.query(AIGrowthTreasurySettings).first()
        if not settings:
            return

        reserve_balance = settings.ai_reinvestment_reserve if settings.reinvestment_enabled else 0.0
        proposals = db.query(SubscriptionProposal).filter(SubscriptionProposal.status == "PROPOSED").all()

        # Dynamic pipeline bottleneck diagnostics
        total_leads = db.query(B2BLead).count()
        qualified_leads = db.query(B2BLead).filter(B2BLead.score > 50).count()
        contact_verified = db.query(B2BLead).filter(
            (B2BLead.email_verified == True) | (B2BLead.phone_verified == True)
        ).count()
        
        emails_sent = db.query(B2BLead).filter(B2BLead.email_sequence_stage > 0).count()
        replies = db.query(B2BLead).filter(B2BLead.status == "REPLIED").count()
        
        # Determine constraints
        contact_constraint = False
        if qualified_leads > 10 and (contact_verified / qualified_leads) < 0.4:
            contact_constraint = True
            
        outreach_constraint = False
        if emails_sent > 10 and (replies / emails_sent) < 0.08:
            outreach_constraint = True
            
        discovery_constraint = False
        if total_leads < 30:
            discovery_constraint = True

        for prop in proposals:
            can_afford = reserve_balance >= prop.monthly_cost
            can_comfortably_afford = reserve_balance >= (prop.monthly_cost * 2)

            is_constrained = False
            if prop.agent_id == "discovery":
                is_constrained = discovery_constraint and not outreach_constraint
            elif prop.agent_id in ("email_sales", "contact_intelligence"):
                is_constrained = contact_constraint or outreach_constraint
            elif prop.agent_id in ("closing", "followup"):
                is_constrained = (replies > 2 and settings.realised_revenue < 100000)

            # Determine justification state based on constraints and affordability
            if settings.bootstrap_mode:
                if not can_afford:
                    if is_constrained:
                        prop.justification_status = "FOUNDER REVIEW"
                    else:
                        prop.justification_status = "NOT JUSTIFIED"
                else:
                    if is_constrained:
                        if can_comfortably_afford:
                            prop.justification_status = "FOUNDER REVIEW" if prop.confidence == "LOW" else "FUNDABLE FROM AI RESERVE"
                        else:
                            prop.justification_status = "FOUNDER REVIEW"
                    else:
                        prop.justification_status = "WATCH"
            else:
                if can_afford:
                    prop.justification_status = "FUNDABLE FROM AI RESERVE"
                else:
                    prop.justification_status = "FOUNDER REVIEW"
        db.commit()
