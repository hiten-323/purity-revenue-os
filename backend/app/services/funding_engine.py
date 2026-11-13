from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.models import B2BLead, AgentCommission, AgentToolUpgrade

# Default splits for the 4 agent channels
DEFAULT_SPLITS = {
    "discovery_scoring": 0.25,    # 25% to Discovery
    "outreach_channels": 0.35,    # 35% to Outreach
    "proposal_negotiation": 0.20, # 20% to Proposal
    "followup_closing": 0.20,     # 20% to Closing
}

SEED_UPGRADES = [
    {
        "agent_key": "discovery_scoring",
        "tool_name": "Google Maps API Premium",
        "upgrade_cost": 1500.0,
        "projected_uplift": 8000.0,
        "roi_multiplier": 5.3,
        "description": "Unlocks higher Google Maps query limits and precise business categorization data verification."
    },
    {
        "agent_key": "discovery_scoring",
        "tool_name": "Cerebras LLM Speed Tier",
        "upgrade_cost": 2500.0,
        "projected_uplift": 15000.0,
        "roi_multiplier": 6.0,
        "description": "High-throughput Cerebras LLM access for instant fit analysis and context parsing."
    },
    {
        "agent_key": "outreach_channels",
        "tool_name": "Zoho SMTP Pro Tier",
        "upgrade_cost": 2000.0,
        "projected_uplift": 12000.0,
        "roi_multiplier": 6.0,
        "description": "Higher daily SMTP sending limits to prevent sequence blockages and dispatch delays."
    },
    {
        "agent_key": "outreach_channels",
        "tool_name": "WhatsApp Business API Credits",
        "upgrade_cost": 3000.0,
        "projected_uplift": 18000.0,
        "roi_multiplier": 6.0,
        "description": "Scales direct automated WhatsApp messaging credits for follow-ups."
    },
    {
        "agent_key": "proposal_negotiation",
        "tool_name": "Premium Digital Proposal Builder",
        "upgrade_cost": 1800.0,
        "projected_uplift": 9000.0,
        "roi_multiplier": 5.0,
        "description": "Professional dynamic HTML proposal templates with interactive digital signature fields."
    },
    {
        "agent_key": "followup_closing",
        "tool_name": "Auto-SMS Fallback Gateway",
        "upgrade_cost": 1200.0,
        "projected_uplift": 6000.0,
        "roi_multiplier": 5.0,
        "description": "Sends auto-SMS messages to decision-makers when email bounces or WhatsApp goes unread for 48 hours."
    }
]

class AgentFundingService:
    @staticmethod
    def seed_upgrades_if_empty(db: Session):
        count = db.query(AgentToolUpgrade).count()
        if count == 0:
            for item in SEED_UPGRADES:
                upgrade = AgentToolUpgrade(
                    agent_key=item["agent_key"],
                    tool_name=item["tool_name"],
                    upgrade_cost=item["upgrade_cost"],
                    projected_uplift=item["projected_uplift"],
                    roi_multiplier=item["roi_multiplier"],
                    status="LOCKED",
                    description=item["description"]
                )
                db.add(upgrade)
            db.commit()

    @staticmethod
    def calculate_deal_commission(db: Session, lead_id: int, actual_cash: float, overrides: dict = None) -> list:
        # Prevent double calculation for the same won deal
        existing = db.query(AgentCommission).filter(AgentCommission.lead_id == lead_id).first()
        if existing:
            return []

        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            return []

        pool = actual_cash * 0.05  # strict 5% commission rate
        splits = overrides if overrides else DEFAULT_SPLITS

        # Normalize splits to ensure they sum to 1.0
        total_split = sum(splits.values())
        if total_split <= 0:
            splits = DEFAULT_SPLITS
            total_split = 1.0

        records = []
        for agent_key, fraction in splits.items():
            agent_fraction = fraction / total_split
            amount = pool * agent_fraction
            
            comm = AgentCommission(
                agent_key=agent_key,
                lead_id=lead_id,
                amount_earned=amount,
                amount_spent=0.0,
                notes=f"5% commission pool split ({(agent_fraction * 100):.1f}%) on {lead.company} B2B sale of INR {actual_cash}",
                created_at=datetime.utcnow()
            )
            db.add(comm)
            records.append(comm)

        db.commit()
        
        # Check if any LOCKED tool upgrades can now be unlocked due to new balance
        AgentFundingService.auto_update_lock_status(db)
        
        return records

    @staticmethod
    def auto_update_lock_status(db: Session):
        AgentFundingService.seed_upgrades_if_empty(db)
        pnl = AgentFundingService.get_agent_pnl(db)
        upgrades = db.query(AgentToolUpgrade).filter(AgentToolUpgrade.status.in_(["LOCKED", "UNLOCKED"])).all()
        for upg in upgrades:
            agent_bal = pnl.get(upg.agent_key, {}).get("net_balance", 0.0)
            if agent_bal >= upg.upgrade_cost:
                upg.status = "UNLOCKED"
            else:
                upg.status = "LOCKED"
        db.commit()

    @staticmethod
    def get_agent_pnl(db: Session) -> dict:
        AgentFundingService.seed_upgrades_if_empty(db)
        
        agents = {
            "discovery_scoring": {
                "name": "Discovery & Fit Scoring Agent",
                "revenue_influenced": 0.0,
                "commission_earned": 0.0,
                "commission_spent": 0.0,
                "net_balance": 0.0,
                "active_upgrades": []
            },
            "outreach_channels": {
                "name": "Outreach & Sequences Agent",
                "revenue_influenced": 0.0,
                "commission_earned": 0.0,
                "commission_spent": 0.0,
                "net_balance": 0.0,
                "active_upgrades": []
            },
            "proposal_negotiation": {
                "name": "Proposal & Pricing Agent",
                "revenue_influenced": 0.0,
                "commission_earned": 0.0,
                "commission_spent": 0.0,
                "net_balance": 0.0,
                "active_upgrades": []
            },
            "followup_closing": {
                "name": "Follow-up & Closing Agent",
                "revenue_influenced": 0.0,
                "commission_earned": 0.0,
                "commission_spent": 0.0,
                "net_balance": 0.0,
                "active_upgrades": []
            }
        }

        # Calculate revenue influenced:
        # Every won B2B Lead contributes its estimated value to the agents
        # (Since they pair-program/cooperate to drive every closed deal)
        won_deals = db.query(B2BLead).filter(B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])).all()
        for d in won_deals:
            val = d.estimated_value or 0.0
            agents["discovery_scoring"]["revenue_influenced"] += val
            agents["outreach_channels"]["revenue_influenced"] += val
            agents["proposal_negotiation"]["revenue_influenced"] += val
            agents["followup_closing"]["revenue_influenced"] += val

        # Calculate commissions
        earned_sums = db.query(
            AgentCommission.agent_key,
            func.sum(AgentCommission.amount_earned).label("earned"),
            func.sum(AgentCommission.amount_spent).label("spent")
        ).group_by(AgentCommission.agent_key).all()

        for row in earned_sums:
            ak = row.agent_key
            if ak in agents:
                agents[ak]["commission_earned"] = float(row.earned or 0.0)
                agents[ak]["commission_spent"] = float(row.spent or 0.0)

        # Net balance calculation
        for ak in agents:
            agents[ak]["net_balance"] = max(0.0, agents[ak]["commission_earned"] - agents[ak]["commission_spent"])

        # Populate active/purchased upgrades
        purchased = db.query(AgentToolUpgrade).filter(AgentToolUpgrade.status == "PURCHASED").all()
        for p in purchased:
            if p.agent_key in agents:
                agents[p.agent_key]["active_upgrades"].append({
                    "tool_name": p.tool_name,
                    "cost": p.upgrade_cost,
                    "roi": p.roi_multiplier
                })

        return agents

    @staticmethod
    def approve_and_execute_upgrade(db: Session, upgrade_id: int) -> bool:
        upg = db.query(AgentToolUpgrade).filter(AgentToolUpgrade.id == upgrade_id).first()
        if not upg or upg.status == "PURCHASED":
            return False

        pnl = AgentFundingService.get_agent_pnl(db)
        agent_bal = pnl.get(upg.agent_key, {}).get("net_balance", 0.0)

        if agent_bal < upg.upgrade_cost:
            # Insufficient funds - strict rule constraint
            return False

        # Create spending record
        spend = AgentCommission(
            agent_key=upg.agent_key,
            lead_id=0, # system transaction
            amount_earned=0.0,
            amount_spent=upg.upgrade_cost,
            notes=f"Self-funded tool purchase: {upg.tool_name} (Cost: INR {upg.upgrade_cost})",
            created_at=datetime.utcnow()
        )
        db.add(spend)

        # Mark upgrade purchased
        upg.status = "PURCHASED"
        upg.approved_at = datetime.utcnow()
        db.commit()

        # Re-evaluate remaining locked states
        AgentFundingService.auto_update_lock_status(db)
        return True
