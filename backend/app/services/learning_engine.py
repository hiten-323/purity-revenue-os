"""
Learning & Continuous Optimization Engine for Purity Revenue OS V2.0.
Tracks conversion analytics, objection frequency patterns, channel performance,
and provides calibrated parameter logging.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

_log = logging.getLogger("learning_engine")


@dataclass
class ChannelPerformanceMetrics:
    channel: str
    total_outreach: int
    deliveries_verified: int
    responses_received: int
    conversions_achieved: int
    response_rate_pct: float
    conversion_rate_pct: float


@dataclass
class LearningAuditReport:
    generated_at: str
    total_leads_tracked: int
    total_paid_conversions: int
    conversions_by_source: Dict[str, int]
    conversions_by_segment: Dict[str, int]
    objection_distribution: Dict[str, int]
    channel_performance: Dict[str, ChannelPerformanceMetrics]
    optimization_recommendations: List[str]


class LearningEngine:
    """
    Computes feedback loops across lead sources, segments, objections,
    and channel conversions to guide continuous optimization.
    """

    @classmethod
    def generate_learning_report(cls, db: Session, days_lookback: int = 30) -> LearningAuditReport:
        """
        Analyzes CRM interactions, order reconciliations, and lead conversions over lookback period.
        """
        from app.models.models import B2BLead, LeadInteraction, EmailDraft
        from app.services.customer_segmentation import CustomerSegmentationEngine

        cutoff = datetime.utcnow() - timedelta(days=days_lookback)
        leads = db.query(B2BLead).all()
        total_leads = len(leads)

        # Track conversions
        conversions_by_src: Dict[str, int] = {}
        conversions_by_seg: Dict[str, int] = {}
        total_conversions = 0

        for lead in leads:
            is_converted = (getattr(lead, "status", "") == "PAID_CONVERSION" or
                            "PURCHASE_COMPLETED" in str(getattr(lead, "notes", "")))
            if is_converted:
                total_conversions += 1
                src = getattr(lead, "lead_source", "unknown") or "unknown"
                conversions_by_src[src] = conversions_by_src.get(src, 0) + 1

                seg = CustomerSegmentationEngine.classify(lead).name
                conversions_by_seg[seg] = conversions_by_seg.get(seg, 0) + 1

        # Track objections from LeadInteraction
        interactions = db.query(LeadInteraction).filter(LeadInteraction.created_at >= cutoff).all()
        objections: Dict[str, int] = {
            "Price / Cost": 0,
            "Existing Brand Loyalty (Nescafe/Bru)": 0,
            "Brand Unfamiliarity": 0,
            "Not Interested / No Requirement": 0,
            "Wrong Number / Left Company": 0,
            "Other": 0
        }

        for ix in interactions:
            remark = str(getattr(ix, "notes", "") or getattr(ix, "outcome", "")).lower()
            if "price" in remark or "expensive" in remark or "cost" in remark:
                objections["Price / Cost"] += 1
            elif "nescafe" in remark or "bru" in remark or "current supplier" in remark:
                objections["Existing Brand Loyalty (Nescafe/Bru)"] += 1
            elif "unknown brand" in remark or "unfamiliar" in remark:
                objections["Brand Unfamiliarity"] += 1
            elif "not interested" in remark or "no need" in remark:
                objections["Not Interested / No Requirement"] += 1
            elif "wrong number" in remark or "wrong contact" in remark:
                objections["Wrong Number / Left Company"] += 1

        # Channel performance analysis
        channels = ["PHONE", "EMAIL", "WHATSAPP"]
        perf_data: Dict[str, ChannelPerformanceMetrics] = {}

        for ch in channels:
            ch_ixs = [i for i in interactions if str(getattr(i, "channel", "")).upper() == ch]
            ch_total = len(ch_ixs)
            ch_resp = len([i for i in ch_ixs if getattr(i, "direction", "") == "inbound" or "REPLIED" in str(getattr(i, "outcome", ""))])
            ch_conv = len([i for i in ch_ixs if "CONVERSION" in str(getattr(i, "outcome", "")) or "ORDER" in str(getattr(i, "outcome", ""))])

            resp_rate = round((ch_resp / max(ch_total, 1)) * 100, 1)
            conv_rate = round((ch_conv / max(ch_total, 1)) * 100, 1)

            perf_data[ch] = ChannelPerformanceMetrics(
                channel=ch,
                total_outreach=ch_total,
                deliveries_verified=ch_total,
                responses_received=ch_resp,
                conversions_achieved=ch_conv,
                response_rate_pct=resp_rate,
                conversion_rate_pct=conv_rate
            )

        # Formulate actionable optimization recommendations
        recommendations = []
        if total_conversions == 0:
            recommendations.append(
                "Immediate focus: Prioritize outreach to the 46 Priority A (80-100 score) leads with Gourmet Duo Mini (₹570) COD offer."
            )
        if objections["Price / Cost"] > 0:
            recommendations.append(
                "Emphasize cost-per-cup calculation (Bold pure coffee at ~₹6.50/cup vs cafe cup) in primary email copy."
            )
        recommendations.append(
            "Continuously enrich decision-maker names and GSTIN before voice calling to maximize call pickup trust."
        )

        return LearningAuditReport(
            generated_at=datetime.utcnow().isoformat(),
            total_leads_tracked=total_leads,
            total_paid_conversions=total_conversions,
            conversions_by_source=conversions_by_src,
            conversions_by_segment=conversions_by_seg,
            objection_distribution=objections,
            channel_performance=perf_data,
            optimization_recommendations=recommendations
        )
