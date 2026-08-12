from app.services.providers import MarketplaceProvider
from app.models.models import AgentLog
from sqlalchemy.orm import Session
from datetime import datetime
from .sales_agent import FlipkartSalesAgent
from .analytics_agent import FlipkartAnalyticsAgent
from .marketing_agent import FlipkartMarketingAgent
from .invoice_agent import FlipkartInvoiceAgent
from .inventory_agent import FlipkartInventoryAgent
from .pricing_agent import FlipkartPricingAgent
from .competitor_agent import FlipkartCompetitorAgent

class FlipkartDirectorAI:
    def __init__(self, db: Session, provider: MarketplaceProvider):
        self.db = db
        self.provider = provider
        self.sales = FlipkartSalesAgent(provider)
        self.analytics = FlipkartAnalyticsAgent(provider)
        self.marketing = FlipkartMarketingAgent(provider)
        self.invoice = FlipkartInvoiceAgent(provider)
        self.inventory = FlipkartInventoryAgent(provider)
        self.pricing = FlipkartPricingAgent(provider)
        self.competitor = FlipkartCompetitorAgent(provider)

    def _log_action(self, action: str, payload: dict):
        log_entry = AgentLog(
            agent_name="Flipkart Director AI",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload
        )
        self.db.add(log_entry)
        self.db.commit()

    async def generate_director_report(self):
        sales_data = await self.provider.get_sales()
        revenue = sales_data.get("revenue", 0)
        
        # Basic rule engine
        roas = sales_data.get("roas", 0)
        recommended_actions = []
        if roas < 3.5:
            recommended_actions.append("Launch coupon campaign for weekend")
        
        report = {
            "marketplace": "Flipkart",
            "health_score": 75,
            "risk_score": 45,
            "revenue": revenue,
            "top_issue": "High RoAS cost",
            "top_opportunity": "Weekend Coupon Drop",
            "recommended_actions": recommended_actions
        }
        
        self._log_action("Generated Flipkart Report", report)
        return report
