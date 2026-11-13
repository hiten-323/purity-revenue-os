from sqlalchemy.orm import Session
from app.agents.amazon.director_ai import AmazonDirectorAI
from app.agents.flipkart.director_ai import FlipkartDirectorAI
from app.agents.blinkit.director_ai import BlinkitDirectorAI
from app.agents.b2b.director_ai_v2 import B2BDirectorAIV2
from app.agents.tender.tender_director_v2 import TenderDirectorV2
from app.services.providers import MarketplaceProvider, B2BProvider, TenderProvider
from app.models.models import AgentLog
from datetime import datetime

class SalesVPAI:
    def __init__(self, db: Session, amazon_provider: MarketplaceProvider, flipkart_provider: MarketplaceProvider, blinkit_provider: MarketplaceProvider, b2b_provider: B2BProvider, tender_provider: TenderProvider):
        self.db = db
        self.amazon_director = AmazonDirectorAI(db, amazon_provider)
        self.flipkart_director = FlipkartDirectorAI(db, flipkart_provider)
        self.blinkit_director = BlinkitDirectorAI(db, blinkit_provider)
        self.b2b_director = B2BDirectorAIV2(db, b2b_provider)
        self.tender_director = TenderDirectorV2(db, tender_provider)


    def _log_action(self, action: str, payload: dict):
        log_entry = AgentLog(
            agent_name="Sales VP AI",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload
        )
        self.db.add(log_entry)
        self.db.commit()

    async def generate_sales_vp_report(self):
        amz_report = await self.amazon_director.generate_director_report()
        flip_report = await self.flipkart_director.generate_director_report()
        blinkit_report = await self.blinkit_director.generate_director_report()
        
        b2b_report = await self.b2b_director.generate_director_report()
        tender_report = await self.tender_director.generate_director_report()

        marketplace_reports = [amz_report, flip_report, blinkit_report]
        
        best_marketplace = max(marketplace_reports, key=lambda x: x["revenue"])["marketplace"]
        highest_growth = "Blinkit" # Placeholder
        highest_risk = max(marketplace_reports, key=lambda x: x["risk_score"])["marketplace"]

        actions = []
        for r in marketplace_reports:
            actions.extend(r.get("recommended_actions", []))
            
        actions.extend(b2b_report.get("recommended_actions", []))
        actions.extend(tender_report.get("recommended_actions", []))

        marketplace_revenue = amz_report["revenue"] + flip_report["revenue"] + blinkit_report["revenue"]

        # Rule engine priorities
        ceo_action = "Maintain current strategy."
        if highest_risk == "Blinkit":
            ceo_action = "Increase Blinkit inventory by 40%"
        if b2b_report.get("hot_leads", 0) > 10:
            ceo_action = actions[-2] if len(actions) > 1 else actions[0] # Try to grab B2B recommendation

        report_data = {
            "marketplace_revenue": marketplace_revenue,
            "b2b_pipeline": b2b_report["pipeline_value"],
            "tender_opportunities": tender_report["pipeline_value"],
            "b2b_hot_leads": b2b_report["hot_leads"],
            "b2b_corporate_opps": 9, # Mock from instructions
            "best_marketplace": best_marketplace,
            "highest_growth": highest_growth,
            "highest_risk": highest_risk,
            "ceo_action": ceo_action,
            "reports": marketplace_reports
        }

        self._log_action("Generated Sales VP Report", {"best_marketplace": best_marketplace, "b2b_pipeline": b2b_report["pipeline_value"]})

        return report_data
