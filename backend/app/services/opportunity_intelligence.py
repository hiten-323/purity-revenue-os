"""
Intelligence Layer — derives assessment from the Truth Layer. Writes nothing.

Every function here is a pure read over LeadEvidence rows plus verified lead
fields. Nothing computed here is persisted onto the lead: buying score,
confidence, classification, opportunity score and next best action are all
recalculated whenever evidence changes.

WHY THIS SEPARATION IS ENFORCED
-------------------------------
Derived values previously lived on the lead row. Two engines wrote
coffee_buying_score — the discovery classifier (category-based) and the
evidence scorer — and the second silently overwrote the first, so every
verified distributor collapsed to 0 and dropped out of the founder's call
queue. Nobody could tell which engine had produced a given number or when.
Storing derivations in the fact store makes them stale and unexplainable, so
they are not stored at all.

TWO DIMENSIONS, NOT ONE
-----------------------
Buying Potential answers "do they need coffee". Commercial Fit answers "are
they a good customer for us" — margin, order size against our minimum, and
geographic efficiency. A 600-room hotel with a superb breakfast programme but
sub-minimum margin is a worse opportunity than a small distributor with
moderate signals and repeat-order potential, and a single score cannot say so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

# Per-source reliability. A fact from Google Places is not equal to a keyword
# guessed from a business name, and the confidence score must reflect that.
SOURCE_CONFIDENCE = {
    "Google Places API": 0.95,
    "Google Maps": 0.90,
    "Official Website": 0.85,
    "Government Tender Portal": 0.95,
    "GST Portal": 0.95,
    "FSSAI": 0.90,
    "IndiaMART": 0.65,
    "TradeIndia": 0.65,
    "LinkedIn": 0.75,
    # Multi-source corroborated during enrichment (Perplexity + Brave +
    # IndiaMART + TradeIndia agreeing on the same number). Was absent from this
    # table and silently fell to the 0.5 default, which dragged the weighted
    # confidence of phone-only leads below every band threshold.
    "Contact Enrichment": 0.70,
    "Business Name Heuristic": 0.40,
    "Category Inference": 0.35,
}

# Distances from the Abohar base, in km. Geographic efficiency is a real
# commercial input: a Rs 50k margin in Mumbai costs days of founder travel.
CITY_DISTANCE_KM = {
    "abohar": 0, "fazilka": 40, "malout": 45, "sri ganganagar": 60,
    "muktsar": 65, "ferozepur": 85, "bathinda": 90, "faridkot": 95,
    "moga": 130, "barnala": 140, "sangrur": 170, "ludhiana": 200,
    "patiala": 230, "jalandhar": 240, "amritsar": 250, "chandigarh": 290,
    "delhi": 400, "jaipur": 480, "mumbai": 1500, "bengaluru": 2200,
    "chennai": 2400, "hyderabad": 1700, "pune": 1550, "kolkata": 1800,
}

HOT, WARM, COLD, REJECT = "HOT", "WARM", "COLD", "REJECT"

# ── Classification bands, calibrated to the signals actually available ───────
# The originally specified bands (HOT > 60 @ 0.7, WARM > 40 @ 0.6) assume all
# six evidence signals are collectable. Three are not: employee count (no
# headcount source), beverage licence (no FSSAI/GST lookup) and tender (only
# reachable for public bodies, none in the current set). Measured across the 93
# Maps-verified leads, the observed maximum is 60 — Suto Cafe, carrying a
# verified breakfast amenity, food-service category and 100+ reviews. HOT at
# "> 60" was therefore unreachable by construction: 0 of 93 could ever qualify,
# and 76 of 82 non-disqualified leads collapsed into one undifferentiated band.
#
# These bands are set against the achievable range instead, at the natural
# breaks in the observed distribution:
#   >= 45  six hotels/cafes carrying three or more corroborating signals
#   >= 30  nineteen leads with a category signal plus corroboration
#   >= 15  the nurture tier
#   < 15   nothing but a phone number
#
# RAISE THESE as the missing signals come online — the ceiling moves to ~105
# once headcount and beverage licence are collectable, at which point the
# original 60/40 thresholds become the right ones again.
HOT_SCORE,  HOT_CONF  = 45, 0.75
WARM_SCORE, WARM_CONF = 30, 0.70
COLD_SCORE            = 15

# ── Opportunity Score blend ─────────────────────────────────────────────────
# Buying evidence deliberately outweighs commercial fit. At an even-handed
# 40/35 the ordering was dominated by commercial fit, because fit is generous
# to any nearby reseller (35% category margin, repeat orders, short distance)
# while buying evidence is hard to earn — it needs a verified amenity, a
# corporate-bookings page or a real tender. The result was that COLD
# distributors outranked every HOT hotel and cafe, so the classification the
# founder sees disagreed with the order they were called in.
#
# Evidence of need now leads. Commercial fit still matters and still demotes
# a below-floor-margin account 1,500 km away — it just no longer decides the
# queue on its own.
W_BUYING          = 0.55
W_COMMERCIAL_FIT  = 0.25
W_CONFIDENCE      = 0.20


@dataclass
class Assessment:
    lead_id: int
    company: str
    buying_score: int = 0
    confidence: float = 0.0
    commercial_fit: int = 0
    opportunity_score: int = 0
    classification: str = REJECT
    evidence: list = field(default_factory=list)
    commercial_notes: list = field(default_factory=list)
    disqualifiers: list = field(default_factory=list)
    recommended_workflow: str = ""
    next_best_action: str = ""
    reasoning: str = ""

    def as_dict(self) -> dict:
        return {
            "lead_id": self.lead_id, "company": self.company,
            "buying_potential": {"score": self.buying_score,
                                 "confidence": round(self.confidence, 2),
                                 "evidence": self.evidence,
                                 "disqualifiers": self.disqualifiers},
            "commercial_fit": {"score": self.commercial_fit,
                               "notes": self.commercial_notes},
            "opportunity_score": self.opportunity_score,
            "classification": self.classification,
            "recommended_workflow": self.recommended_workflow,
            "next_best_action": self.next_best_action,
            "reasoning": self.reasoning,
        }


def score_buying_potential(evidence_rows) -> tuple[int, float, list, list]:
    """
    Aggregate the Truth Layer. Returns (score, confidence, positives, disqualifiers).

    Confidence is the weight-weighted mean of per-row confidence, so a score
    built from one heuristic guess reads very differently from the same score
    built from Google Places plus a verified tender.
    """
    score = 0
    weighted_conf = 0.0
    weight_sum = 0.0
    positives, disqualifiers = [], []

    for e in evidence_rows:
        w = (e.weight_positive or 0) - (e.weight_negative or 0)
        score += w
        if w:
            weighted_conf += float(e.confidence or 0.0) * abs(w)
            weight_sum += abs(w)
        item = {"signal": e.signal_type, "value": e.value, "weight": w,
                "source": e.source, "confidence": e.confidence,
                "verified": bool(e.verified),
                "collected_at": e.collected_at.isoformat() if e.collected_at else None}
        (disqualifiers if w < 0 else positives).append(item)

    confidence = (weighted_conf / weight_sum) if weight_sum else 0.0
    positives.sort(key=lambda x: x["weight"], reverse=True)
    return score, confidence, positives, disqualifiers


def score_commercial_fit(lead) -> tuple[int, list]:
    """
    Can we serve this profitably? 0–100, independent of how much they want coffee.
    Uses only verified facts and our own published policy — never a guess about
    the customer's payment behaviour, which we have no data for until they order.
    """
    from app.services.business_policies import MarginPolicy, SamplePolicy

    notes = []
    score = 50                       # neutral until evidence moves it

    # Margin against our own floor.
    try:
        from app.api.endpoints import MARGIN_RATES
        rate = MARGIN_RATES.get((lead.division or "corporate").lower(), 0.30)
    except Exception:
        rate = 0.30
    margin_pct = rate * 100
    floor = getattr(MarginPolicy, "MIN_GROSS_MARGIN_PCT", 28.0)
    target = getattr(MarginPolicy, "TARGET_MARGIN_PCT", 31.0)
    if margin_pct >= target:
        score += 20
        notes.append(f"Category margin {margin_pct:.0f}% meets the {target:.0f}% target")
    elif margin_pct >= floor:
        score += 8
        notes.append(f"Category margin {margin_pct:.0f}% clears the {floor:.0f}% floor")
    else:
        score -= 25
        notes.append(f"Category margin {margin_pct:.0f}% is BELOW the {floor:.0f}% floor")

    # Order size against our minimum.
    monthly_kg = float(getattr(lead, "expected_monthly_consumption_kg", 0) or 0)
    if not monthly_kg:
        est_annual = float(getattr(lead, "estimated_value", 0) or 0)
        realization = getattr(MarginPolicy, "BLENDED_REALIZATION_PER_KG", 1400.0)
        monthly_kg = (est_annual / realization / 12) if realization else 0.0
    min_kg = getattr(SamplePolicy, "MIN_ORDER_KG", 20)
    if monthly_kg >= min_kg * 3:
        score += 15
        notes.append(f"Est. {monthly_kg:.0f} kg/mo — comfortably above the {min_kg} kg minimum")
    elif monthly_kg >= min_kg:
        score += 5
        notes.append(f"Est. {monthly_kg:.0f} kg/mo — meets the {min_kg} kg minimum")
    else:
        score -= 20
        notes.append(f"Est. {monthly_kg:.0f} kg/mo — BELOW the {min_kg} kg minimum order")

    # Geographic efficiency.
    city = (lead.city or "").strip().lower()
    dist = CITY_DISTANCE_KM.get(city)
    if dist is None:
        notes.append(f"Distance from Abohar unknown for '{lead.city or '?'}'")
    elif dist <= 50:
        score += 15
        notes.append(f"{dist} km from Abohar — same-day servicing")
    elif dist <= 150:
        score += 8
        notes.append(f"{dist} km from Abohar — regional, efficient to service")
    elif dist <= 400:
        score -= 5
        notes.append(f"{dist} km from Abohar — courier only, higher servicing cost")
    else:
        score -= 20
        notes.append(f"{dist} km from Abohar — outside efficient servicing range")

    # Repeat-order potential by business model.
    div = (lead.division or "").lower()
    if div in ("distributor", "wholesaler", "grocery", "kirana_store", "retail_chain"):
        score += 10
        notes.append("Reseller — recurring replenishment rather than one-off")
    elif div in ("corporate_gifting", "gifting"):
        score -= 5
        notes.append("Gifting — seasonal, weaker repeat pattern")

    return max(0, min(100, score)), notes


def _engagement_factor(lead) -> tuple[float, str]:
    """Recent real engagement, from recorded outcomes only."""
    outcome = (getattr(lead, "call_outcome_last", "") or "").lower()
    if outcome in ("interested", "need_sample", "need_proposal"):
        return 1.25, f"recent positive outcome ({outcome})"
    if outcome in ("call_back", "busy"):
        return 1.10, f"recent contact ({outcome})"
    if outcome in ("not_interested", "already_supplier"):
        return 0.5, f"declined ({outcome})"
    last = getattr(lead, "last_updated", None)
    if last:
        days = (datetime.utcnow() - last).days
        if days > 90:
            return 0.85, f"no interaction in {days} days"
    return 1.0, "no recorded engagement yet"


def assess(lead, evidence_rows) -> Assessment:
    """Full assessment. Pure — persists nothing."""
    a = Assessment(lead_id=lead.id, company=lead.company or "")
    a.buying_score, a.confidence, a.evidence, a.disqualifiers = score_buying_potential(evidence_rows)
    a.commercial_fit, a.commercial_notes = score_commercial_fit(lead)
    eng_factor, eng_note = _engagement_factor(lead)

    # Opportunity Score: a weighted blend, deliberately NOT multiplied by the
    # revenue estimate. Multiplying by a modelled figure let a fabricated
    # Rs 62.6L lead outrank a verified distributor 20:1. Revenue enters only
    # through commercial fit, which is bounded.
    buying_norm = max(0, min(100, a.buying_score))
    blended = ((buying_norm * W_BUYING)
               + (a.commercial_fit * W_COMMERCIAL_FIT)
               + (a.confidence * 100 * W_CONFIDENCE))
    a.opportunity_score = int(max(0, min(100, round(blended * eng_factor))))

    # Classification on score AND confidence, so a high score built from weak
    # sources cannot present as HOT.
    if a.buying_score <= -50 or any(d["weight"] <= -100 for d in a.disqualifiers):
        a.classification = REJECT
    elif a.buying_score >= HOT_SCORE and a.confidence >= HOT_CONF:
        a.classification = HOT
    elif a.buying_score >= WARM_SCORE and a.confidence >= WARM_CONF:
        a.classification = WARM
    elif a.buying_score >= COLD_SCORE:
        a.classification = COLD
    elif a.disqualifiers:
        # Below COLD AND carrying a real negative signal — genuinely disqualified.
        a.classification = REJECT
    else:
        # Below the COLD threshold with nothing against it. This is the common
        # case for a lead whose evidence has not been collected yet: score 0, no
        # signals either way. REJECT here was hiding 582 phone-reachable leads
        # that were never actually disqualified — only unassessed. Unqualified
        # is COLD (still workable, ranks low); REJECT is reserved for a recorded
        # disqualifier, so a lead only leaves the queue when something is
        # genuinely wrong with it, never for want of a scan.
        a.classification = COLD

    has_email = bool((lead.email or "").strip())
    has_phone = bool((lead.phone or lead.whatsapp_number or "").strip())
    if a.classification == REJECT:
        a.recommended_workflow = "none"
        a.next_best_action = ("Archive — " + (a.disqualifiers[0]["signal"]
                              if a.disqualifiers else "no observable coffee buying signal"))
    elif not (has_email or has_phone):
        a.recommended_workflow = "discovery"
        a.next_best_action = "Enrich contact details — no phone or email on record"
    elif has_phone:
        a.recommended_workflow = "phone_first"
        a.next_best_action = ("Founder call" if a.classification in (HOT, WARM)
                              else "WhatsApp introduction")
    else:
        a.recommended_workflow = "email_first"
        a.next_best_action = "Personalised email"

    top = a.evidence[0]["signal"] if a.evidence else "no positive signal"
    a.reasoning = (f"{a.classification}: buying {a.buying_score} at "
                   f"{a.confidence:.0%} confidence, commercial fit {a.commercial_fit}/100, "
                   f"{eng_note}. Strongest signal: {top}.")
    return a
