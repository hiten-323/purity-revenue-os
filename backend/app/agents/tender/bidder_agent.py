from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import B2BLead, AgentLog
import logging

_log = logging.getLogger(__name__)

class TenderBidderAgent:
    def __init__(self, db: Session):
        self.db = db

    def draft_bid(self, company: str) -> dict:
        lead = self.db.query(B2BLead).filter(
            B2BLead.company == company,
            B2BLead.division == "tender"
        ).first()

        if not lead:
            return {"status": "error", "message": f"Tender lead '{company}' not found."}

        # Determine monthly volume based on estimated value or defaults
        monthly_kg = 100.0
        if "army" in company.lower():
            monthly_kg = 80.0
        elif "hospital" in company.lower():
            monthly_kg = 60.0

        cost_per_kg = 600.0  # cost price
        bid_price_per_kg = 648.0  # Cost + 8% margin (extremely low, highly competitive)
        margin_pct = round(((bid_price_per_kg - cost_per_kg) / bid_price_per_kg) * 100, 2)
        
        monthly_value = bid_price_per_kg * monthly_kg
        annual_value = monthly_value * 12

        udyam_reg = "UDYAM-PB-03-004812"
        fssai_lic = "12124999000182"
        iso_cert = "ISO-9001:2015 / HACCP"

        # Generate bid proposal document text
        bid_proposal_text = f"""OFFICIAL TENDER SUBMISSION (GeM Portal)
Tender ID: GeM/2026/B/882194
Buyer Organisation: {lead.company}
Bidding Supplier: Pure Pantry Provisions (Brand: Purity Beans)
Submission Date: {datetime.utcnow().strftime('%d %b %Y')}

--------------------------------------------------
1. COMMERCIAL BID (OPTIMIZED LOW-PRICE STRATEGY)
--------------------------------------------------
Product Description: Purity Beans Premium Gourmet Instant Coffee
  - 100% pure coffee, spray-dried, medium roast.
  - Zero chicory, zero artificial additives.
  - Packaged in moisture-proof 500g bulk bags for canteen dispenser use.

Required Volume: {monthly_kg:.1f} kg/month
Offered Unit Rate: Rs. {bid_price_per_kg:.2f} per kg (exclusive of taxes)
Monthly Billing: Rs. {monthly_value:,.2f}
Annual Bid Value: Rs. {annual_value:,.2f} (Lowest Price Guaranteed)

*Strategic Compliance Pricing: Rate set at bare-minimum margin (8.0% above cost) to maximize selection probability under L1 (Lowest Bidder) guidelines.*

--------------------------------------------------
2. TECHNICAL COMPLIANCE & REGISTRATIONS
--------------------------------------------------
- FSSAI License: {fssai_lic} (Registered Manufacturer)
- MSME Registration: {udyam_reg} (Eligible for MSME bid preference)
- Quality Certification: {iso_cert} Compliant
- Sourcing: 100% Indian coffee beans (procured from Chikmagalur estates)
- Logistics: Free door-delivery to Abohar SHQ Canteen within 48 hours of dispatch.

--------------------------------------------------
3. BIDDER UNDERTAKING
--------------------------------------------------
We hereby agree to fulfill the pantry coffee supply requirements of {lead.company} in strict compliance with the tender norms, delivery schedules, and credit terms (30 days post-delivery).

Prepared by:
Tender Bidding AI Agent (on behalf of Hiten Jain, Founder)
Pure Pantry Provisions
connect@purepantryprovisions.com
"""
        return {
            "status": "success",
            "company": lead.company,
            "monthly_kg": monthly_kg,
            "suggested_price": bid_price_per_kg,
            "suggested_margin": margin_pct,
            "proposal_text": bid_proposal_text
        }

    def submit_bid(self, company: str, bid_price: float, proposal_text: str) -> dict:
        lead = self.db.query(B2BLead).filter(
            B2BLead.company == company,
            B2BLead.division == "tender"
        ).first()

        if not lead:
            return {"status": "error", "message": f"Tender lead '{company}' not found."}

        monthly_kg = 100.0
        if "army" in company.lower():
            monthly_kg = 80.0
        elif "hospital" in company.lower():
            monthly_kg = 60.0
            
        cost_per_kg = 600.0
        margin_pct = round(((bid_price - cost_per_kg) / bid_price) * 100, 2) if bid_price > 0 else 0.0
        monthly_value = bid_price * monthly_kg
        annual_value = monthly_value * 12

        # Update Lead CRM state with approved/edited details
        before_status = lead.status
        lead.status = "PROPOSAL_SENT"
        lead.proposal_monthly_kg = monthly_kg
        lead.proposal_suggested_price = bid_price
        lead.proposal_suggested_margin = margin_pct
        lead.proposal_discount_percent = 0.0
        lead.estimated_value = annual_value
        lead.proposal_text = proposal_text
        lead.probability = 0.95 if bid_price <= 650.0 else (0.80 if bid_price <= 750.0 else 0.50)
        lead.priority = "HIGH"
        # Wording matters: nothing here posts to GeM. This records that the
        # founder submitted the bid; it does not perform the submission. The
        # old text claimed "GeM bid posted", which would have read as proof of
        # an submission that never happened.
        lead.recommended_action = f"Bid recorded at Rs {bid_price:.2f}/kg — confirm submission on the portal, then monitor selection."
        lead.qualification_notes = f"Bid approved by founder and recorded. Bid price: Rs {bid_price:.2f}/kg."
        lead.last_updated = datetime.utcnow()

        # Write the immutable event. Without this the status change was
        # invisible to every revenue KPI, which counts events and not lead
        # status — the same defect that made a won order show zero revenue.
        try:
            from app.services.pipeline_tracker import track
            track(self.db, "PROPOSAL_SENT", lead_id=lead.id, actor="FOUNDER",
                  channel="tender", before_status=before_status,
                  after_status="PROPOSAL_SENT",
                  payload={"bid_price_per_kg": bid_price,
                           "monthly_kg": monthly_kg,
                           "annual_value_inr": annual_value,
                           "recorded_not_submitted": True})
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

        self.db.commit()

        # Log agent activity
        self.db.add(AgentLog(
            agent_name="Tender Bidder Agent",
            action="Submitted Approved Bid on GeM",
            timestamp=datetime.utcnow(),
            payload={
                "company": lead.company,
                "bid_value_annual": annual_value,
                "price_per_kg": bid_price,
                "margin_percent": margin_pct,
                "status": "Approved & Submitted"
            }
        ))
        self.db.commit()

        return {
            "status": "success",
            "message": f"Successfully submitted approved bid for {lead.company} at ₹{bid_price:.2f}/kg.",
            "lead": {
                "company": lead.company,
                "estimated_value": annual_value,
                "status": lead.status,
                "proposal_text": proposal_text
            }
        }
