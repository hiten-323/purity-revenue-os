"""
Quotation, Pricing & Sample Dispatch Engine for Purity Revenue OS V2.0.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.services.product_fit_engine import PRODUCT_CATALOGUE, ProductFitEngine

_log = logging.getLogger("quotation_sample_engine")
GST_RATE = 0.18
WHOLESALE_MOQ_JARS = 12
DISTRIBUTOR_TRADE_DISCOUNT = 0.25
DISTRIBUTOR_CASH_DISCOUNT = 0.05

@dataclass
class WholesaleQuotation:
    quote_id: str
    sku: str
    product_name: str
    unit_mrp: float
    quantity_units: int
    gross_mrp_total: float
    base_discount_pct: float
    base_discount_amount: float
    trade_discount_pct: float
    trade_discount_amount: float
    cash_discount_pct: float
    cash_discount_amount: float
    net_taxable_value: float
    gst_rate_pct: float
    gst_amount: float
    final_invoice_amount: float
    effective_unit_cost: float
    effective_margin_pct: float
    payment_terms: str
    dispatch_lead_days: int
    validity_days: int
    blend_disclosure: str
    freight_terms: str
    founder_approval_required: bool
    approval_status: str
    below_reference_quantity: bool

class QuotationSampleEngine:
    """Computes wholesale quotations, starter offers and sample tracking."""
    @classmethod
    def generate_wholesale_quotation(cls, sku: str, quantity_units: int = WHOLESALE_MOQ_JARS,
                                     payment_terms: str = "ADVANCE_OR_COD") -> WholesaleQuotation:
        sku_details = ProductFitEngine.get_sku_details(sku)
        if not sku_details:
            raise ValueError(f"Unknown wholesale SKU: {sku}")
        if quantity_units <= 0:
            raise ValueError("quantity_units must be greater than zero")
        unit_mrp = float(sku_details["mrp"])
        gross_mrp_total = unit_mrp * quantity_units
        cash_discount_pct = DISTRIBUTOR_CASH_DISCOUNT if payment_terms in ("ADVANCE_OR_COD", "ADVANCE", "COD") else 0.0
        trade_discount_amt = round(gross_mrp_total * DISTRIBUTOR_TRADE_DISCOUNT, 2)
        cash_discount_amt = round(gross_mrp_total * cash_discount_pct, 2)
        total_discount_amt = round(trade_discount_amt + cash_discount_amt, 2)
        final_invoice_amount = round(gross_mrp_total - total_discount_amt, 2)
        effective_unit_cost = round(final_invoice_amount / quantity_units, 2)
        effective_margin_pct = round((total_discount_amt / gross_mrp_total) * 100, 1)
        net_taxable = round(final_invoice_amount / (1 + GST_RATE), 2)
        gst_amt = round(final_invoice_amount - net_taxable, 2)
        disclosure = "Contains chicory." if sku_details.get("has_chicory") else "Pure coffee, Zero Chicory."
        quote_id = f"Q-{datetime.utcnow().strftime('%Y%m%d')}-{sku}-{quantity_units}"
        return WholesaleQuotation(
            quote_id=quote_id, sku=sku,
            product_name=f"{sku_details['family']} {sku_details['variant']}",
            unit_mrp=unit_mrp, quantity_units=quantity_units, gross_mrp_total=gross_mrp_total,
            base_discount_pct=(DISTRIBUTOR_TRADE_DISCOUNT + cash_discount_pct) * 100,
            base_discount_amount=total_discount_amt,
            trade_discount_pct=DISTRIBUTOR_TRADE_DISCOUNT * 100,
            trade_discount_amount=trade_discount_amt,
            cash_discount_pct=cash_discount_pct * 100, cash_discount_amount=cash_discount_amt,
            net_taxable_value=net_taxable, gst_rate_pct=GST_RATE * 100, gst_amount=gst_amt,
            final_invoice_amount=final_invoice_amount, effective_unit_cost=effective_unit_cost,
            effective_margin_pct=effective_margin_pct, payment_terms=payment_terms,
            dispatch_lead_days=2, validity_days=3, blend_disclosure=disclosure,
            freight_terms="Freight excluded; buyer bears actual freight charges.",
            founder_approval_required=True, approval_status="PENDING_FOUNDER_APPROVAL",
            below_reference_quantity=quantity_units < WHOLESALE_MOQ_JARS
        )

    @classmethod
    def generate_starter_offer(cls, pack_code: str = "VARIETY_BOX") -> Dict[str, Any]:
        if pack_code == "GOURMET_DUO_MINI":
            return {
                "sku": "PB-GOURMET-DUO-50",
                "name": "Gourmet Duo Mini (Purista 50g + Purica 50g)",
                "price": 570.0, "mrp": 588.0, "shipping": "Free",
                "payment_modes": ["Cash on Delivery (COD)", "UPI / Cards / Net Banking"],
                "url": "https://www.p3online.in/products/gourmet-duo-mini",
                "blend_disclosure": "Pure coffee, Zero Chicory."
            }
        return {
            "sku": "PB-VARIETY-4x50",
            "name": "Variety Box (4 x 50g Jars: Purica, Purista, Bold, Ultra Blend)",
            "price": 877.0, "mrp": 1012.0, "shipping": "Free",
            "payment_modes": ["Cash on Delivery (COD)", "UPI / Cards / Net Banking"],
            "url": "https://www.p3online.in/products/variety-box",
            "blend_disclosure": "Ultra Blend component contains roasted chicory. Purica, Purista, and Bold are pure coffee."
        }

    @classmethod
    def record_sample_dispatch(cls, lead_id: int, sample_sku: str, courier_name: str,
                               awb_number: str, db: Session) -> Dict[str, Any]:
        from app.models.models import LeadInteraction
        now = datetime.utcnow()
        feedback_due = now + timedelta(days=5)
        interaction = LeadInteraction(
            lead_id=lead_id, channel="manual", direction="outbound",
            outcome="SAMPLE_DISPATCHED",
            notes=f"Commercial sample dispatched: SKU={sample_sku}, Courier={courier_name}, AWB={awb_number}. Feedback SLA due: {feedback_due.strftime('%Y-%m-%d')}",
            created_at=now
        )
        db.add(interaction)
        db.commit()
        return {
            "lead_id": lead_id, "sample_sku": sample_sku, "courier_name": courier_name,
            "awb_number": awb_number, "dispatch_date": now.isoformat(),
            "feedback_due_date": feedback_due.strftime("%Y-%m-%d"), "status": "SAMPLE_IN_TRANSIT"
        }
