"""
Purity Beans Revenue Scoring Engine
5-factor model that scores every B2B lead 0-100 and assigns a revenue tier.

Factors:
  Revenue Potential    35%
  Ease of Conversion   30%
  Contact Availability 15%
  Buying Signal        10%
  Geography            10%

Tiers:
  90+  Platinum
  70+  Gold
  50+  Silver
  <50  Bronze
"""

from __future__ import annotations
from app.models.models import B2BLead

# Segment base scores (Revenue Potential + Ease of Conversion)
_SEGMENT_REVENUE = {
    "gifting":          90,
    "distributor":      85,
    "grocery":          80,
    "corporate_pantry": 82,
    "horeca":           65,
    "cafe":             50,
    "kirana":           30,
    "corporate":        68,
    "retail":           72,
    # Demand-side instant-coffee consumers
    "govt_canteen":        88,
    "facility_management": 86,
    "catering_contractor": 84,
    "corporate_gifting":   88,
    "wholesaler":          85,
    "retail_chain":        82,
    "kirana_store":        60,
    "industrial_canteen":  80,
    "education_mess":      80,
    "corporate_office":   78,
    "hospital":            76,
    "event_catering":      74,
    "hotel_canteen":       72,
    "guest_house":         70,
}

_SEGMENT_CONVERSION = {
    "gifting":          92,
    "distributor":      85,
    "grocery":          80,
    "corporate_pantry": 74,
    "horeca":           60,
    "cafe":             48,
    "kirana":           28,
    "corporate":        70,
    "retail":           70,
    # Demand-side instant-coffee consumers (govt harder = procurement cycle)
    "corporate_gifting":   80,
    "kirana_store":        78,
    "retail_chain":        72,
    "wholesaler":          82,
    "corporate_office":    72,
    "industrial_canteen":  68,
    "education_mess":      66,
    "guest_house":         66,
    "catering_contractor": 66,
    "facility_management": 64,
    "event_catering":      64,
    "hospital":            62,
    "hotel_canteen":       60,
    "govt_canteen":        50,
}

_SIZE_BONUS = {
    "Enterprise":   12,
    "Mid-Market":    6,
    "SMB":           0,
}

# Cities sorted by Purity Beans market priority
_TIER1_CITIES = {"bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "new delhi"}
_TIER2_CITIES = {"chennai", "kolkata", "ahmedabad", "surat", "jaipur", "lucknow", "chandigarh"}

_STATUS_SIGNAL = {
    "COLD":             0,
    "DISCOVERED":       5,
    "QUALIFIED":       15,
    "EMAIL_SENT":      20,
    "REPLIED":         60,
    "MEETING_BOOKED":  75,
    "MEETING_COMPLETED": 80,
    "SAMPLE_SENT":     85,
    "PROPOSAL_SENT":   90,
    "ORDER_WON":      100,
    "ONBOARDED":      100,
}


def _segment_from_lead(lead: B2BLead) -> str:
    div = (lead.division or "").lower()
    if div in _SEGMENT_REVENUE:
        return div
    ind = (lead.industry or "").lower()
    if "gift" in ind:
        return "gifting"
    if "distributor" in ind or "wholesale" in ind:
        return "distributor"
    if "hotel" in ind or "hospitality" in ind or "resort" in ind:
        return "horeca"
    if "grocery" in ind or "retail" in ind or "supermarket" in ind:
        return "grocery"
    if "cafe" in ind or "coffee" in ind:
        return "cafe"
    if "kirana" in ind:
        return "kirana"
    return "corporate"


def score_lead(lead: B2BLead) -> dict:
    seg = _segment_from_lead(lead)
    size_bonus = _SIZE_BONUS.get(lead.company_size or "Mid-Market", 0)

    # 1. Revenue Potential (35%)
    rev_base = _SEGMENT_REVENUE.get(seg, 60)
    if lead.estimated_value and lead.estimated_value > 500_000:
        rev_base = min(100, rev_base + 8)
    elif lead.estimated_value and lead.estimated_value > 200_000:
        rev_base = min(100, rev_base + 4)
    revenue_potential = min(100, rev_base + size_bonus)

    # 2. Ease of Conversion (30%)
    conv_base = _SEGMENT_CONVERSION.get(seg, 60)
    ease_of_conversion = min(100, conv_base + size_bonus // 2)

    # 3. Contact Availability (15%)
    contact_score = 0
    if lead.contact_name and lead.contact_name.strip():
        contact_score += 30
    if lead.email and "@" in lead.email:
        contact_score += 35
    if lead.phone and len(lead.phone.strip()) >= 10:
        contact_score += 35

    # 4. Buying Signal (10%)
    status_sig = _STATUS_SIGNAL.get(lead.status or "DISCOVERED", 5)
    opens_bonus = min(20, (lead.email_opens or 0) * 10)
    clicks_bonus = min(15, (lead.email_clicks or 0) * 8)
    buying_signal = min(100, status_sig + opens_bonus + clicks_bonus)

    # 5. Geography (10%)
    city = (lead.city or "").lower().strip()
    if city in _TIER1_CITIES:
        geography = 100
    elif city in _TIER2_CITIES:
        geography = 70
    elif city:
        geography = 45
    else:
        geography = 20

    final_score = round(
        revenue_potential * 0.35
        + ease_of_conversion * 0.30
        + contact_score * 0.15
        + buying_signal * 0.10
        + geography * 0.10
    )

    if final_score >= 90:
        tier = "Platinum"
    elif final_score >= 70:
        tier = "Gold"
    elif final_score >= 50:
        tier = "Silver"
    else:
        tier = "Bronze"

    # Estimate annual value if not set
    monthly_kg = lead.expected_monthly_consumption_kg or 0
    if monthly_kg > 0:
        annual_value = monthly_kg * 700 * 12
    elif lead.estimated_value and lead.estimated_value > 0:
        annual_value = lead.estimated_value
    else:
        # Default by segment
        defaults = {
            "gifting": 240_000, "distributor": 180_000, "grocery": 120_000,
            "corporate_pantry": 80_000, "horeca": 60_000, "cafe": 40_000,
            "kirana": 20_000, "corporate": 72_000, "retail": 90_000,
        }
        annual_value = defaults.get(seg, 60_000)

    return {
        "segment": seg,
        "final_score": final_score,
        "revenue_tier": tier,
        "estimated_annual_value": annual_value,
        "factor_breakdown": {
            "revenue_potential": round(revenue_potential),
            "ease_of_conversion": round(ease_of_conversion),
            "contact_availability": round(contact_score),
            "buying_signal": round(buying_signal),
            "geography": round(geography),
        },
    }


def score_all_leads(db) -> dict:
    """Score every lead and persist results. Returns summary stats."""
    from sqlalchemy import text
    # Ensure columns exist (SQLite ALTER TABLE)
    for col, typedef in [
        ("final_score", "INTEGER DEFAULT 0"),
        ("revenue_tier", "VARCHAR DEFAULT 'Bronze'"),
        ("estimated_annual_value", "FLOAT DEFAULT 0"),
        ("segment", "VARCHAR DEFAULT 'corporate'"),
    ]:
        try:
            db.execute(text(f"ALTER TABLE b2b_leads ADD COLUMN {col} {typedef}"))
            db.commit()
        except Exception:
            pass  # column already exists

    leads = db.query(B2BLead).all()
    counts = {"Platinum": 0, "Gold": 0, "Silver": 0, "Bronze": 0}
    total_value = 0.0

    for lead in leads:
        result = score_lead(lead)
        lead.final_score = result["final_score"]
        lead.revenue_tier = result["revenue_tier"]
        lead.estimated_annual_value = result["estimated_annual_value"]
        lead.segment = result["segment"]
        counts[result["revenue_tier"]] += 1
        total_value += result["estimated_annual_value"]

    db.commit()
    return {
        "scored": len(leads),
        "tier_breakdown": counts,
        "total_pipeline_value": round(total_value),
        "platinum_value": round(sum(
            score_lead(l)["estimated_annual_value"]
            for l in leads if (l.final_score or 0) >= 90
        )),
    }
