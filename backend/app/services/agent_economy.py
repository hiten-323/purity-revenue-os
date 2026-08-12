from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import AIAgentPerformance, AIGrowthTreasurySettings, SubscriptionProposal, LeadAttributionPath, B2BLead, Product, EmailDraft

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
        
        # Calculate current software costs: sum of active subscription costs
        active_cost = db.query(SubscriptionProposal).filter(SubscriptionProposal.status == "ACTIVE").all()
        current_software_cost = sum(p.monthly_cost for p in active_cost)

        # Check if there are won opportunities without a matching product/COGS in Product table
        won_leads = db.query(B2BLead).filter(B2BLead.status.in_(["ORDER_WON", "PAYMENT_VERIFIED"])).all()
        any_unknown_cogs = False
        for l in won_leads:
            if not l.purchase_sku:
                any_unknown_cogs = True
                break
            prod = db.query(Product).filter(Product.sku == l.purchase_sku).first()
            if not prod or prod.cost_price is None or prod.cost_price <= 0:
                any_unknown_cogs = True
                break

        realised_margin_val = settings.realised_margin
        ai_reserve_val = settings.ai_reinvestment_reserve

        # If we have payments but no verified COGS, mark realised_margin/reserve as PENDING
        if any_unknown_cogs and settings.realised_margin == 0.0 and settings.realised_revenue > 0:
            margin_display = "PENDING"
            reserve_display = "PENDING"
            available_display = "PENDING"
        else:
            margin_display = realised_margin_val
            reserve_display = ai_reserve_val
            available_display = ai_reserve_val - current_software_cost

        return {
            "realised_revenue": settings.realised_revenue,
            "realised_margin": margin_display,
            "ai_reinvestment_reserve": reserve_display,
            "current_software_cost": current_software_cost,
            "available_for_reinvestment": available_display,
            "reinvestment_rate": settings.reinvestment_rate,
            "reinvestment_enabled": settings.reinvestment_enabled,
            "bootstrap_mode": settings.bootstrap_mode
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
            "orders_influenced": a.orders_influenced,
            "realised_revenue": a.realised_revenue,
            "realised_margin": a.realised_margin,
            "founder_minutes_used": a.founder_minutes_used,
            "cost": a.cost,
            "roi": a.realised_margin / a.cost if a.cost > 0 else 0.0,
            "attribution_confidence": a.attribution_confidence
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
    def verify_payment_and_allocate(db: Session, lead_id: int, actual_cash: float) -> dict:
        AgentEconomyService.seed_if_empty(db)
        
        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            return {"status": "error", "message": "Lead not found"}

        # Prevent double allocation
        existing_path = db.query(LeadAttributionPath).filter(LeadAttributionPath.lead_id == lead_id).first()
        if existing_path:
            return {"status": "error", "message": "Attribution has already been calculated for this deal"}

        # COGS known verification
        cogs_known = False
        margin = 0.0
        
        if lead.purchase_sku:
            product = db.query(Product).filter(Product.sku == lead.purchase_sku).first()
            if product and product.cost_price is not None and product.cost_price > 0:
                cogs_known = True
                if product.selling_price and product.selling_price > 0:
                    margin_rate = (product.selling_price - product.cost_price) / product.selling_price
                    margin = actual_cash * margin_rate
                else:
                    qty = lead.proposal_monthly_kg or 1.0
                    margin = actual_cash - (product.cost_price * qty)

        # Update global treasury revenue
        settings = db.query(AIGrowthTreasurySettings).first()
        settings.realised_revenue += actual_cash
        
        reserve_added = 0.0
        if cogs_known:
            settings.realised_margin += margin
            if settings.reinvestment_enabled:
                reserve_added = margin * settings.reinvestment_rate
                settings.ai_reinvestment_reserve += reserve_added

        # Record actual conversion path
        path_status = {
            "discovery": True,
            "qualification": bool(lead.score and lead.score > 0 or lead.qualification_notes),
            "contact_intelligence": bool(lead.email_verified or lead.phone_verified or lead.contact_searched_at),
            "email_sales": False,
            "followup": bool(lead.email_sequence_stage and lead.email_sequence_stage > 1),
            "reply_intelligence": bool(lead.status == "REPLIED" or (lead.email_opens and lead.email_opens > 0)),
            "founder_call_coach": bool((lead.call_attempts and lead.call_attempts > 0) or (lead.call_duration_seconds and lead.call_duration_seconds > 0)),
            "sample_conversion": bool(lead.sample_requested or lead.sample_sku),
            "proposal": bool(lead.proposal_suggested_price or lead.proposal_monthly_kg or lead.proposal_text),
            "closing": bool(lead.status in ("ORDER_WON", "PAYMENT_VERIFIED")),
            "reorder": bool(lead.reorder_sku),
            "government_tender": bool((lead.division and lead.division.lower() == "govt_canteen") or (lead.industry and lead.industry.lower() == "government"))
        }

        # Check if email drafts or sequence stage indicates email_sales
        draft_count = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id).count()
        if draft_count > 0 or (lead.email_sequence_stage and lead.email_sequence_stage > 0):
            path_status["email_sales"] = True

        # Calculate sum of weights for active agents
        active_weight_sum = sum(ATTRIBUTION_SPLITS[agent_id] for agent_id, active in path_status.items() if active)
        if active_weight_sum == 0:
            active_weight_sum = 1.0
            path_status["discovery"] = True

        # Distribute splits to active agents only
        for agent_id, default_weight in ATTRIBUTION_SPLITS.items():
            if path_status.get(agent_id, False):
                split_weight = default_weight / active_weight_sum
                
                agent = db.query(AIAgentPerformance).filter(AIAgentPerformance.agent_id == agent_id).first()
                if agent:
                    agent.realised_revenue += (split_weight * actual_cash)
                    if cogs_known:
                        agent.realised_margin += (split_weight * margin)
                    agent.orders_influenced += 1
                    
                    # Log path
                    log_path = LeadAttributionPath(
                        lead_id=lead_id,
                        stage="PAYMENT_VERIFIED",
                        agent_id=agent_id,
                        weight=split_weight,
                        occurred_at=datetime.utcnow()
                    )
                    db.add(log_path)

        db.commit()

        # Re-evaluate proposals
        AgentEconomyService.evaluate_upgrade_proposals(db)

        return {
            "status": "success",
            "realised_revenue": actual_cash,
            "realised_margin": margin if cogs_known else "UNKNOWN",
            "reserve_added": reserve_added,
            "cogs_known": cogs_known
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

        reserve_balance = settings.ai_reinvestment_reserve
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
