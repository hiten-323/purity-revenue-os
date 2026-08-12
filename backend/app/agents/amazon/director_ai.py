from app.services.providers import MarketplaceProvider
from app.models.models import AgentLog
from sqlalchemy.orm import Session
from datetime import datetime
from .sales_agent import AmazonSalesAgent
from .analytics_agent import AmazonAnalyticsAgent
from .marketing_agent import AmazonMarketingAgent
from .invoice_agent import AmazonInvoiceAgent
from .inventory_agent import AmazonInventoryAgent
from .pricing_agent import AmazonPricingAgent
from .competitor_agent import AmazonCompetitorAgent

class AmazonDirectorAI:
    def __init__(self, db: Session, provider: MarketplaceProvider):
        self.db = db
        self.provider = provider
        self.sales = AmazonSalesAgent(provider)
        self.analytics = AmazonAnalyticsAgent(provider)
        self.marketing = AmazonMarketingAgent(provider)
        self.invoice = AmazonInvoiceAgent(provider)
        self.inventory = AmazonInventoryAgent(provider)
        self.pricing = AmazonPricingAgent(provider)
        self.competitor = AmazonCompetitorAgent(provider)

    def _log_action(self, action: str, payload: dict):
        log_entry = AgentLog(
            agent_name="Amazon Director AI",
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
        if roas > 4.0:
            recommended_actions.append("Increase exact match bids")
            
        report = {
            "marketplace": "Amazon",
            "health_score": 87,
            "risk_score": 18,
            "revenue": revenue,
            "top_issue": "Inventory low",
            "top_opportunity": "Increase ad budget",
            "recommended_actions": recommended_actions
        }
        
        self._log_action("Generated Amazon Report", report)
        return report
