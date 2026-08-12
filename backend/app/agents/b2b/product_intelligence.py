from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.models import B2BLead, AgentLog
from datetime import datetime

class ProductIntelligenceAgent:
    """
    Dedicated Product Intelligence Agent.
    Receives taste, packaging, and price complaints from lost opportunities and feedback,
    and recommends targeted B2B SKUs/packs (Trial Pack, Bundle, Corporate Pack, Gift Box)
    to optimize B2B conversion rates.
    """

    def __init__(self, db: Session):
        self.db = db

    def _log(self, action: str, payload: dict):
        self.db.add(AgentLog(
            agent_name="Product Intelligence Agent",
            action=action,
            timestamp=datetime.utcnow(),
            payload=payload,
        ))
        self.db.commit()

    def get_product_recommendations(self) -> dict:
        """
        Analyzes CRM feedback and lost reasons to determine strategic product improvements and SKU suggestions.
        """
        # Count lost reasons
        cold_leads = self.db.query(B2BLead).filter(B2BLead.status == "COLD").all()
        
        taste_complaints = 0
        packaging_complaints = 0
        price_complaints = 0
        total_complaints = 0
        
        for lead in cold_leads:
            reason = str(lead.lost_reason or "").lower()
            if "taste" in reason or "flavor" in reason or "poor taste" in reason:
                taste_complaints += 1
                total_complaints += 1
            elif "packaging" in reason or "design" in reason:
                packaging_complaints += 1
                total_complaints += 1
            elif "price" in reason or "expensive" in reason or "cost" in reason:
                price_complaints += 1
                total_complaints += 1
                
        # Also check feedback sliders of active leads (where score < 7)
        active_leads = self.db.query(B2BLead).filter(B2BLead.status != "COLD").all()
        for lead in active_leads:
            if lead.sample_taste and lead.sample_taste < 7:
                taste_complaints += 1
                total_complaints += 1
            if lead.sample_packaging and lead.sample_packaging < 7:
                packaging_complaints += 1
                total_complaints += 1

        # Determine recommendations based on counts
        recommendations = []
        sku_suggestions = []
        
        if taste_complaints > 0 or total_complaints == 0:
            recommendations.append({
                "type": "Sensory Adjustment",
                "problem": "Taste feedback indicates bitterness or high acidity in dark roasts.",
                "solution": "Reformulate the premium dark instant coffee blend to include smoother Arabica beans.",
                "suggested_product": "Smooth Arabica Dark Roast Pilot Pack"
            })
            sku_suggestions.append("PURITY-ARABICA-DARK-TRIAL")
            
        if price_complaints > taste_complaints or total_complaints == 0:
            recommendations.append({
                "type": "Introductory Pricing",
                "problem": "High entry price point blocks wholesale commitments.",
                "solution": "Introduce a 5kg trial pack with a 10% introductory wholesale discount.",
                "suggested_product": "5kg Trial Pack Bundle"
            })
            sku_suggestions.append("PURITY-5KG-TRIAL-PACK")
            
        if packaging_complaints > 0:
            recommendations.append({
                "type": "Packaging Redesign",
                "problem": "Premium aesthetic is not fully conveyed by current jars.",
                "solution": "Introduce custom gold-foiled tin canisters for corporate gifting and canteen display.",
                "suggested_product": "Corporate Tin Canister Edition"
            })
            sku_suggestions.append("PURITY-CORP-TIN-GOLD")

        # Default recommendations if no data
        if not recommendations:
            recommendations.append({
                "type": "General SKU Expansion",
                "problem": "No significant sensory or price complaints recorded.",
                "solution": "Launch corporate gifting hampers for the upcoming festive season to capture high-margin gifting budgets.",
                "suggested_product": "Festive Corporate Gift Box"
            })
            sku_suggestions.append("PURITY-CORP-GIFT-BOX")

        result = {
            "total_complaints_analyzed": total_complaints,
            "complaints_breakdown": {
                "taste": taste_complaints,
                "packaging": packaging_complaints,
                "price": price_complaints
            },
            "recommendations": recommendations,
            "suggested_skus": sku_suggestions,
            "last_audit": datetime.utcnow().isoformat()
        }
        
        self._log("Product Intelligence Audit Completed", {"total_complaints": total_complaints})
        return result
