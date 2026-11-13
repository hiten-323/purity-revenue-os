from app.services.providers import MarketplaceProvider
from app.models.models import AgentLog
from sqlalchemy.orm import Session
from datetime import datetime
from .sales_agent import BlinkitSalesAgent
from .analytics_agent import BlinkitAnalyticsAgent
from .marketing_agent import BlinkitMarketingAgent
from .invoice_agent import BlinkitInvoiceAgent
from .inventory_agent import BlinkitInventoryAgent
from .pricing_agent import BlinkitPricingAgent
from .competitor_agent import BlinkitCompetitorAgent

class BlinkitDirectorAI:
    def __init__(self, db: Session, provider: MarketplaceProvider):
        self.db = db
        self.provider = provider
        self.sales = BlinkitSalesAgent(provider)
        self.analytics = BlinkitAnalyticsAgent(provider)
        self.marketing = BlinkitMarketingAgent(provider)
        self.invoice = BlinkitInvoiceAgent(provider)
        self.inventory = BlinkitInventoryAgent(provider)
        self.pricing = BlinkitPricingAgent(provider)
        self.competitor = BlinkitCompetitorAgent(provider)

    def _log_action(self, action: str, payload: dict):
        log_entry = AgentLog(
            agent_name="Blinkit Director AI",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload
        )
        self.db.add(log_entry)
        self.db.commit()

    async def generate_director_report(self):
        sales_data = await self.provider.get_sales()
        revenue = sales_data.get("revenue", 0)
        
        # Rule engine
        inv_data = await self.provider.get_inventory()
        recommended_actions = []
        if inv_data.get("days_remaining", 0) < 5:
            recommended_actions.append("Urgent replenishment required in NCR")
        
        report = {
            "marketplace": "Blinkit",
            "health_score": 92,
            "risk_score": 8,
            "revenue": revenue,
            "top_issue": "Stockout Risk NCR",
            "top_opportunity": "Expand dark stores",
            "recommended_actions": recommended_actions
        }
        
        self._log_action("Generated Blinkit Report", report)
        return report
