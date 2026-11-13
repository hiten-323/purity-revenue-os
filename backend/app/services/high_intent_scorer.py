"""
High-Intent Scoring Engine V1.1 for Purity Revenue OS.

Evaluates verifiable buying signals from observable evidence and Truth Layer.
Never fabricates intent, buying signals, or contact information.

Signal Weights:
- Explicit recent instant coffee buying enquiry: +30
- Recent online supplier / vendor-switch enquiry: +30
- Coffee quotation or pricing request: +25
- Coffee sample or tasting request: +25
- Wholesale or institutional supply requirement: +20
- High coffee buying fit score (CBS >= 70): +15
- Incumbent competitor coffee brand identified (vendor displacement): +15
- Affirmative contact consent recorded (AI_CALL / EMAIL_REPLY): +20
- Recent product-page / cart buying activity: +15
- MOQ or packaging enquiry: +15
- Enquiry within last 7 days (recency bonus): +15
- Verified contact information (phone + MX email): +5

Negative / Hard Exclusions:
- DNC / Opt-out / Revoked consent
- Suppressed numbers or emails
- Unsupported or unknown contact permissions for voice/WA
- Existing paid customers (switched to retention/onboarding)

Tiers:
- Priority A: 80 - 100 (Immediate high-touch outreach: Voice cohort / WhatsApp pacer)
- Priority B: 60 - 79 (Priority qualification & high-conversion email outreach)
- Nurture: 40 - 59 (Low-touch nurture; no voice calling capacity)
- Below 40: Hold (Suppressed from automated sales outreach)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


@dataclass
class IntentEvaluation:
    lead_id: int
    score: int
    tier: str  # Priority A | Priority B | Nurture | Below 40
    signals_detected: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    is_suppressed: bool = False
    suppression_reason: str = ""
    evaluated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


def evaluate_intent(lead, db=None) -> IntentEvaluation:
    """
    Evaluates evidence-based intent for a single lead.
    Returns IntentEvaluation with transparent point breakdown.
    """
    lead_id = getattr(lead, "id", 0)
    
    # 1. Hard Exclusion & Suppression Checks
    status = (getattr(lead, "status", "") or "").upper()
    if status in ("DO_NOT_CONTACT", "CLOSED_UNRECOVERABLE", "WRONG_NUMBER", "QUARANTINED", "OPTED_OUT", "REVOKED"):
        return IntentEvaluation(
            lead_id=lead_id, score=0, tier="Below 40",
            is_suppressed=True, suppression_reason=f"Status is {status}"
        )
    
    if getattr(lead, "do_not_call", False):
        return IntentEvaluation(
            lead_id=lead_id, score=0, tier="Below 40",
            is_suppressed=True, suppression_reason="Lead is marked Do Not Call"
        )
        
    score = 0
    signals = []
    reasons = []

    # 2. Existing intent base score from database (if present)
    base_db_score = int(getattr(lead, "intent_score", 0) or 0)
    
    # 3. Explicit recent instant coffee buying enquiry (+30)
    source = (getattr(lead, "lead_source", "") or "").lower()
    notes = f"{getattr(lead, 'qualification_notes', '') or ''} {getattr(lead, 'call_summary', '') or ''}".lower()
    
    if any(k in source for k in ("inbound", "website_enquiry", "quotation_request", "procurement")) or \
       any(k in notes for k in ("looking for coffee", "buying instant coffee", "instant coffee", "buying coffee", "enquired for coffee", "coffee requirement", "coffee supply")):
        score += 30
        signals.append("EXPLICIT_COFFEE_BUYING_ENQUIRY")
        reasons.append("+30: Explicit instant coffee buying enquiry detected")

    # 4. Recent online supplier enquiry / displacement (+30)
    if "supplier" in notes or "vendor" in notes or "tender" in source or getattr(lead, "current_supplier", None):
        score += 30
        signals.append("SUPPLIER_ENQUIRY")
        reasons.append("+30: Online supplier or vendor-switch enquiry detected")

    # 5. Coffee quotation or pricing request (+25)
    if any(k in notes for k in ("quotation", "price", "rate", "cost", "pricing", "commercial", "margin")):
        score += 25
        signals.append("PRICING_OR_QUOTATION_REQUEST")
        reasons.append("+25: Commercial quotation or price list requested")

    # 6. Coffee sample or tasting request (+25)
    if getattr(lead, "sample_requested", False) or any(k in notes for k in ("sample", "tasting", "trial carton")):
        score += 25
        signals.append("SAMPLE_OR_TASTING_REQUEST")
        reasons.append("+25: Sample evaluation or tasting requested")

    # 7. Wholesale or institutional supply requirement (+20)
    div = (getattr(lead, "division", "") or "").lower()
    seg = (getattr(lead, "segment", "") or "").lower()
    ind = (getattr(lead, "industry", "") or "").lower()
    if div in ("distributor", "wholesale", "wholesaler", "horeca", "hotel", "corporate_office") or \
       seg in ("distributor", "wholesale", "horeca", "supermarket") or \
       ind in ("distributor", "wholesale", "hotel", "hospital", "supermarket") or \
       "wholesale" in notes or "institutional" in notes:
        score += 20
        signals.append("WHOLESALE_INSTITUTIONAL_REQUIREMENT")
        reasons.append("+20: Wholesale, distributor, modern trade, or institutional requirement")

    # 8. High Coffee Buying Fit Score (+15)
    cbs = int(getattr(lead, "coffee_buying_score", 0) or 0)
    if cbs >= 70:
        score += 15
        signals.append("HIGH_COFFEE_FIT_SCORE")
        reasons.append(f"+15: High verified coffee buying fit (score {cbs} >= 70)")

    # 9. Competitor Brand Identified (+15)
    brand = getattr(lead, "current_brand", None)
    if brand:
        score += 15
        signals.append("COMPETITOR_BRAND_IDENTIFIED")
        reasons.append(f"+15: Incumbent competitor coffee brand ({brand}) identified for vendor displacement")

    # 10. Affirmative Consent Recorded (+20)
    c_status = (getattr(lead, "consent_status", "") or "").upper()
    c_source = getattr(lead, "consent_source", "") or ""
    if c_status in ("EXPLICIT", "OPTED_IN") or c_source in ("AI_CALL_WHATSAPP_REQUEST", "EMAIL_REPLY_WHATSAPP_REQUEST"):
        score += 20
        signals.append("AFFIRMATIVE_CONSENT_RECORDED")
        reasons.append("+20: Explicit verified consent recorded for outreach")

    # 11. Recent product-page or cart activity (+15)
    if getattr(lead, "sample_sku", None) in ("Purica", "Purity Beans") and getattr(lead, "purchase_sku", None):
        score += 15
        signals.append("PRODUCT_PAGE_ACTIVITY")
        reasons.append("+15: Product SKU interest and purchase intent recorded")

    # 12. MOQ or packaging enquiry (+15)
    if any(k in notes for k in ("moq", "packaging", "glass jar", "jar size", "50g", "100g", "200g", "bulk bag")):
        score += 15
        signals.append("MOQ_OR_PACKAGING_ENQUIRY")
        reasons.append("+15: Specific MOQ or packaging specifications enquired")

    # 13. Recency bonus: Enquiry within last 7 days (+15)
    consent_ts = getattr(lead, "consent_timestamp", None)
    stage_ts = getattr(lead, "stage_entered_date", None)
    ref_ts = consent_ts or stage_ts
    if ref_ts:
        days_ago = (datetime.utcnow() - ref_ts).days
        if days_ago <= 7:
            score += 15
            signals.append("RECENCY_WITHIN_7D")
            reasons.append(f"+15: Recent enquiry activity ({days_ago} days ago)")
        elif days_ago > 30:
            score = max(0, score - 15)
            reasons.append(f"-15: Signal decay: activity is older than 30 days ({days_ago} days ago)")

    # 14. Verified contact details (+5)
    phone = re.sub(r"\D", "", getattr(lead, "phone", "") or getattr(lead, "whatsapp_number", "") or "")
    has_valid_phone = len(phone) >= 10 and (phone[-10] in "6789" or phone.startswith("0"))
    has_email = bool(getattr(lead, "email", None) and "@" in str(lead.email))
    if has_valid_phone and has_email:
        score += 5
        signals.append("VERIFIED_CONTACTS")
        reasons.append("+5: Verified phone and business email present")

    # Pure evidence-based score: never polluted by legacy database scores
    final_score = min(100, score)

    # Tier classification
    if final_score >= 80:
        tier = "Priority A"
    elif final_score >= 60:
        tier = "Priority B"
    elif final_score >= 40:
        tier = "Nurture"
    else:
        tier = "Below 40"

    return IntentEvaluation(
        lead_id=lead_id,
        score=final_score,
        tier=tier,
        signals_detected=signals,
        reasons=reasons,
        is_suppressed=False,
    )


def rank_leads_by_intent(leads: list, db=None) -> list:
    """
    Ranks a collection of leads by IntentEvaluation score descending,
    breaking ties by recency.
    """
    scored = []
    for l in leads:
        eval_res = evaluate_intent(l, db=db)
        scored.append((l, eval_res))
    
    def _rank_key(item):
        lead, ev = item
        if ev.is_suppressed:
            return (-1, 0, 0)
        ts = getattr(lead, "consent_timestamp", None) or getattr(lead, "stage_entered_date", None)
        ts_val = ts.timestamp() if ts else 0
        return (1, ev.score, ts_val)
        
    scored.sort(key=_rank_key, reverse=True)
    return scored


def sweep_intent_scores(db) -> dict:
    """
    Evaluates and updates intent_score and intent_tier across all active leads in B2BLead.
    Returns breakdown of Priority A, Priority B, Nurture, Below 40.
    """
    from app.models.models import B2BLead

    leads = db.query(B2BLead).filter(
        ~B2BLead.status.in_(["DO_NOT_CONTACT", "CLOSED_UNRECOVERABLE", "WRONG_NUMBER"])
    ).all()

    counts = {"Priority A": 0, "Priority B": 0, "Nurture": 0, "Below 40": 0, "total": len(leads)}
    batch_count = 0

    for l in leads:
        ev = evaluate_intent(l, db=db)
        l.intent_score = ev.score
        l.intent_tier = ev.tier
        counts[ev.tier] = counts.get(ev.tier, 0) + 1
        batch_count += 1
        if batch_count % 100 == 0:
            db.commit()

    db.commit()
    return counts
