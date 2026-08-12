"""
Revenue Engine — V1.2 layer on top of the Founder Revenue OS.

Reframes the system from lead management to revenue creation:
- data_completeness(lead)     → 0-100 score across the 10 spec fields
- revenue_potential(lead)     → potential / margin / probability / confidence
- automation_split(leads)     → Automation-created vs Founder-convertible pipeline
- expansion_status(leads)     → geographic coverage per expansion stage

Pure compute, no side effects — same contract as the Decision Engine.
"""

from __future__ import annotations
from datetime import datetime

from app.services.decision_engine import STATUS_PROGRESSION, compute_rrs

# ── Expansion stages (Abohar-first concentric model) ─────────────────────────

EXPANSION_STAGES = [
    {"key": "abohar",   "label": "Abohar",        "cities": ["Abohar"]},
    {"key": "local",    "label": "50 km radius",  "cities": ["Fazilka", "Sri Ganganagar", "Ferozepur"]},
    {"key": "punjab",   "label": "Punjab",        "cities": ["Bathinda", "Ludhiana", "Amritsar", "Jalandhar", "Chandigarh", "Patiala"]},
    {"key": "north",    "label": "North India",   "cities": ["Delhi", "Jaipur", "Dehradun", "Agra", "Meerut", "Gurgaon", "Noida"]},
    {"key": "national", "label": "PAN India",     "cities": ["Bangalore", "Mumbai", "Hyderabad", "Pune", "Chennai", "Kolkata", "Ahmedabad"]},
]

# A stage counts as covered when each city has at least this many discovered leads
MIN_LEADS_PER_CITY = 10

# Statuses reachable without any founder time
AUTOMATED_STATUSES = {
    "COLD", "DISCOVERED", "QUALIFIED",
    "INTRO_EMAIL_SENT", "EMAIL_SENT", "WHATSAPP_SENT", "AI_CALLED",
}
# Statuses requiring founder involvement to progress/close
FOUNDER_STATUSES = {
    "FOUNDER_CALLED", "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED",
    "SAMPLE_SENT", "PROPOSAL_SENT",
}
WON_STATUSES = {"ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "ACCOUNT_GROWTH", "UPSELL_OFFERED"}

MARGIN_RATE = 0.31   # blended gross margin


# ── Deterministic revenue estimation (no randomness, no fabrication) ──────────
# Expected ANNUAL coffee-supply value from real signals only: the buyer segment
# (how much coffee that kind of business consumes) × the city tier (market size).
# Same segment+city ⇒ same honest estimate; it differentiates by what we know,
# never by invented precision.

_SEGMENT_BASE_ANNUAL = {
    "distributor": 800_000,
    "wholesaler": 800_000,
    "wholesale": 800_000,
    "modern_trade": 500_000,
    "supermarket": 400_000,
    "grocery_chain": 350_000,
    "retail_kirana": 110_000,
    "corporate_office": 160_000,
    "office_pantry": 150_000,
    "manufacturing": 600_000,
    "facility_management": 400_000,
    "hotel": 240_000,
    "restaurant": 200_000,
    "cafe": 300_000,
    "hospital": 200_000,
    "school": 150_000,
    "college": 200_000,
    "government": 500_000,
    "corporate_gifting": 220_000,
    "gifting": 220_000,
    "private_label": 600_000,
    "exporter": 600_000,
    "institutional_buyer": 400_000,
    "needs_reclassification": 150_000,
    "unknown": 150_000,
}
_METRO = {"delhi","new delhi","mumbai","bengaluru","bangalore","hyderabad","chennai",
          "kolkata","pune","ahmedabad","gurugram","gurgaon","noida"}
_TIER1 = {"chandigarh","ludhiana","amritsar","jalandhar","jaipur","lucknow","kanpur",
          "nagpur","indore","bhopal","patna","surat","vadodara","coimbatore","kochi",
          "mohali","panchkula","patiala"}


def _city_tier_multiplier(city: str) -> float:
    c = (city or "").strip().lower()
    if c in _METRO:  return 2.0
    if c in _TIER1:  return 1.4
    return 1.0       # tier-2/3 towns (Abohar, Fazilka, Sri Ganganagar, …)


def _size_multiplier(reviews: int | None, rating: float | None) -> float:
    """
    Deterministic size proxy from Google reviews (footfall/scale) nudged by
    rating (quality). Real signal only — None ⇒ neutral 1.0, never invented.
    """
    if reviews is None:
        return 1.0
    r = int(reviews)
    if   r >= 500: m = 1.6
    elif r >= 200: m = 1.35
    elif r >= 50:  m = 1.15
    elif r >= 10:  m = 1.0
    else:          m = 0.85     # very few reviews → small outlet
    if rating is not None:
        try:
            if float(rating) >= 4.3: m *= 1.05
            elif float(rating) < 3.0: m *= 0.95
        except (TypeError, ValueError):
            pass
    return m


def estimate_annual_value(segment: str, city: str = "",
                          reviews: int | None = None, rating: float | None = None) -> int:
    """Deterministic expected annual value: segment × city tier × real size
    signal (Google reviews/rating). Same inputs ⇒ same estimate; differentiates
    only by what we actually know."""
    base = _SEGMENT_BASE_ANNUAL.get((segment or "").lower().strip(), 150_000)
    val = base * _city_tier_multiplier(city) * _size_multiplier(reviews, rating)
    return int(round(val / 1000.0) * 1000)


def estimate_basis(segment: str, city: str = "",
                   reviews: int | None = None, rating: float | None = None) -> dict:
    """
    What the number above is actually based on.

    With no review count the size multiplier is 1.0 by design, so the result is
    base x city tier and nothing else - i.e. the category default, identical for
    every business of that type in that city. 66 of 91 groceries carry exactly
    Rs 1,10,000 and all 35 wholesalers exactly Rs 3,60,000 for this reason.

    That is the honest output, but printed alone as "Rs 1.1 L" against a named
    shop it reads as a measurement of THAT shop. Callers use this to label it,
    the same way an unknown contact reads "not recorded yet" rather than blank.
    """
    sized = reviews is not None
    return {
        "value": estimate_annual_value(segment, city, reviews, rating),
        "is_category_default": not sized,
        "basis": (f"sized from {int(reviews)} Google reviews" if sized
                  else f"category default for {segment or 'unknown'} — no size signal for this business"),
    }


# ── Layer: Data Completeness ─────────────────────────────────────────────────

COMPLETENESS_FIELDS = [
    ("company",        lambda l: bool(l.company)),
    ("website",        lambda l: bool(l.website)),
    ("email",          lambda l: bool(l.email)),
    ("verified_email", lambda l: bool(l.email) and (l.email_verification_status or "") in ("VALID", "RISKY_CATCH_ALL", "CATCH_ALL")),
    ("phone",          lambda l: bool(l.phone)),
    ("verified_phone", lambda l: bool(l.phone) and bool(getattr(l, "phone_verified", False))),
    ("whatsapp",       lambda l: bool(l.whatsapp_number) and bool(getattr(l, "phone_verified", False))),
    ("decision_maker", lambda l: bool(l.contact_name) or bool(getattr(l, "decision_maker", None))),
    ("city",           lambda l: bool(l.city)),
    ("revenue_estimate", lambda l: (l.estimated_value or 0) > 0),
]

RE_ENRICH_THRESHOLD = 80   # below this → auto re-enrichment, not founder queues


def data_completeness(lead) -> dict:
    """Per-lead completeness checklist + 0-100 score."""
    checklist = {}
    hit = 0
    for name, fn in COMPLETENESS_FIELDS:
        try:
            ok = bool(fn(lead))
        except Exception:
            ok = False
        checklist[name] = ok
        hit += 1 if ok else 0
    score = round(hit / len(COMPLETENESS_FIELDS) * 100)
    return {"score": score, "checklist": checklist,
            "needs_enrichment": score < RE_ENRICH_THRESHOLD}


# ── Layer: Revenue Potential ─────────────────────────────────────────────────

def revenue_potential(lead) -> dict:
    """
    The founder-facing card: how much is this company worth if won,
    how confident are we in the data, how likely is the deal.
    """
    potential = float(lead.estimated_value or 0)
    if potential <= 0:
        # No stored value → deterministic segment × city-tier × real size signal
        # (Google reviews/rating). Never ₹0, never random.
        potential = float(estimate_annual_value(
            lead.segment or lead.division or "", lead.city or "",
            reviews=getattr(lead, "maps_reviews_count", None),
            rating=getattr(lead, "maps_rating", None)))
    margin = round(potential * MARGIN_RATE)
    prog = STATUS_PROGRESSION.get(lead.status or "", 0)

    # Probability: pipeline progress blended with stored win probability
    stage_prob = min(90, prog * 7)
    stored = float(lead.probability or 0)
    probability = round(max(stage_prob, min(stored, 95)))

    completeness = data_completeness(lead)
    # Confidence = how much we trust the record: verified data + engagement
    rrs = compute_rrs(lead)
    confidence = round(completeness["score"] * 0.6 + rrs * 0.4)

    return {
        "revenue_potential_rs": round(potential),
        "expected_margin_rs": margin,
        "probability_pct": probability,
        "confidence_pct": confidence,
        "data_completeness_pct": completeness["score"],
        "needs_enrichment": completeness["needs_enrichment"],
    }


def opportunity_rank_score(lead) -> float:
    """
    Revenue Opportunity ranking (spec §7): expected margin × confidence ×
    engagement — not just lead status.
    """
    rp = revenue_potential(lead)
    return round(
        rp["expected_margin_rs"] * (rp["confidence_pct"] / 100) * (0.5 + rp["probability_pct"] / 200)
    )


# ── Layer: Automation vs Founder revenue split ───────────────────────────────

def automation_split(leads: list) -> dict:
    """
    Split the live pipeline into:
    - automation bucket: value created with zero founder time
    - founder bucket: value the founder's actions can convert
    - won bucket: gross margin already secured
    """
    auto_value = auto_margin = 0.0
    founder_value = founder_margin = 0.0
    won_value = won_margin = 0.0

    counts = {
        "companies_discovered": 0,
        "verified_emails": 0,
        "verified_phones": 0,
        "ai_qualified": 0,
        "founder_calls": 0,
        "meetings": 0,
        "samples": 0,
        "proposals": 0,
        "orders_won": 0,
    }

    for l in leads:
        v = float(l.estimated_value or 0)
        m = v * MARGIN_RATE
        s = l.status or "COLD"

        counts["companies_discovered"] += 1
        if l.email and (l.email_verification_status or "") in ("VALID", "RISKY_CATCH_ALL", "CATCH_ALL"):
            counts["verified_emails"] += 1
        if l.phone and getattr(l, "phone_verified", False):
            counts["verified_phones"] += 1
        if s == "AI_CALLED":
            counts["ai_qualified"] += 1
        if s in ("FOUNDER_CALLED",):
            counts["founder_calls"] += 1
        if s in ("MEETING_BOOKED", "MEETING_COMPLETED"):
            counts["meetings"] += 1
        if s == "SAMPLE_SENT":
            counts["samples"] += 1
        if s == "PROPOSAL_SENT":
            counts["proposals"] += 1
        if s in WON_STATUSES:
            counts["orders_won"] += 1

        if s in WON_STATUSES:
            won_value += v
            won_margin += m
        elif s in FOUNDER_STATUSES:
            founder_value += v
            founder_margin += m
        elif s in AUTOMATED_STATUSES and s not in ("COLD",):
            auto_value += v
            auto_margin += m

    return {
        "automation": {
            "pipeline_value_rs": round(auto_value),
            "expected_margin_rs": round(auto_margin),
            "stages": ["Discovery", "Email", "WhatsApp", "AI Call", "Qualified"],
            **{k: counts[k] for k in ("companies_discovered", "verified_emails",
                                       "verified_phones", "ai_qualified")},
        },
        "founder": {
            "pipeline_value_rs": round(founder_value),
            "expected_margin_rs": round(founder_margin),
            "stages": ["Founder Call", "Meeting", "Sample", "Proposal", "Negotiation"],
            **{k: counts[k] for k in ("founder_calls", "meetings", "samples", "proposals")},
        },
        "won": {
            "value_rs": round(won_value),
            "gross_margin_rs": round(won_margin),
            "orders_won": counts["orders_won"],
        },
        "combined": {
            "total_pipeline_rs": round(auto_value + founder_value),
            "total_expected_margin_rs": round(auto_margin + founder_margin),
        },
    }


# ── Layer: Geographic Expansion Engine ───────────────────────────────────────

def expansion_status(leads: list) -> dict:
    """
    Coverage per expansion stage. A city is covered when it holds at least
    MIN_LEADS_PER_CITY discovered leads; a stage completes when all its
    cities are covered. Returns the next stage discovery should target.
    """
    from collections import Counter
    city_counts = Counter((l.city or "").strip().lower() for l in leads if l.city)

    stages = []
    next_stage = None
    for stage in EXPANSION_STAGES:
        covered = 0
        city_detail = []
        for city in stage["cities"]:
            n = city_counts.get(city.lower(), 0)
            is_covered = n >= MIN_LEADS_PER_CITY
            covered += 1 if is_covered else 0
            city_detail.append({"city": city, "leads": n, "covered": is_covered})
        pct = round(covered / len(stage["cities"]) * 100)
        complete = pct == 100
        stages.append({
            "key": stage["key"],
            "label": stage["label"],
            "coverage_pct": pct,
            "complete": complete,
            "cities": city_detail,
        })
        if next_stage is None and not complete:
            next_stage = stage["key"]

    return {
        "stages": stages,
        "next_stage": next_stage or "national",
        "next_cities": next(
            ([c["city"] for c in s["cities"] if not c["covered"]]
             for s in stages if s["key"] == (next_stage or "national")),
            [],
        ),
        "generated_at": datetime.utcnow().isoformat(),
    }
