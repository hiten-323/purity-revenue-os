"""
Government Revenue Engine — V1.2 module of the Founder Revenue OS.

Objective: maximize winnable, high-margin, compliant tender opportunities —
not tender count. Every score is computed from the tender's real published
fields plus the company's actual profile. Nothing is fabricated:
- unknown facts stay unknown (None / "UNKNOWN"),
- AI analysis is grounded in the stored tender record only,
- email/bid drafts never claim experience, turnover or certifications
  that are not in COMPANY_PROFILE.
"""

from __future__ import annotations
import os
import re
import json
from datetime import datetime
import logging

_log = logging.getLogger(__name__)

# ── Company profile: ONLY verifiable facts. The founder maintains this. ──────
# Anything not listed here must never appear as a claim in emails or bids.

COMPANY_PROFILE = {
    "legal_name": "Pure Pantry Provisions",
    "brand": "Purity Beans",
    "products": [
        "Purica — freeze-dried instant coffee",
        "Purista — freeze-dried instant coffee",
        "Bold — premium agglomerated instant coffee",
        "Ultra Blend — premium instant coffee",
    ],
    "true_claims": [
        "100% coffee — zero chicory, no fillers",
        "No artificial flavours or additives",
        "Food-grade lead-free glass jars (50g & 100g)",
        "FSSAI licensed",
        "GST registered",
        "MSME registered",
        "PAN India dispatch capability",
    ],
    "forbidden_claims": [
        "past government supplies", "turnover figures", "ISO certification",
        "manufacturing capacity numbers", "client references",
    ],
    "contact": {
        "name": "Hiten Jain",
        "email": "connect@purepantryprovisions.com",
        "phone": "+91 90849 58495",
        "website": "p3online.in",
    },
}

# Founder document locker status. UNKNOWN = founder has not confirmed upload —
# surfaced as a "missing" item, never assumed present.
DOCUMENT_CHECKLIST = [
    {"key": "gst",       "label": "GST Certificate",       "status": os.getenv("DOC_GST", "UNKNOWN")},
    {"key": "pan",       "label": "PAN",                   "status": os.getenv("DOC_PAN", "UNKNOWN")},
    {"key": "fssai",     "label": "FSSAI License",         "status": os.getenv("DOC_FSSAI", "UNKNOWN")},
    {"key": "msme",      "label": "MSME / Udyam",          "status": os.getenv("DOC_MSME", "UNKNOWN")},
    {"key": "catalogue", "label": "Product Catalogue",     "status": os.getenv("DOC_CATALOGUE", "UNKNOWN")},
    {"key": "pricelist", "label": "Institutional Price List", "status": os.getenv("DOC_PRICELIST", "UNKNOWN")},
    {"key": "profile",   "label": "Company Profile",       "status": os.getenv("DOC_PROFILE", "UNKNOWN")},
]

COFFEE_KEYWORDS = {
    "coffee": 100, "instant coffee": 100, "beverage": 55, "beverages": 55,
    "tea": 35, "canteen": 45, "pantry": 50, "refreshment": 45,
    "grocery": 40, "provision": 40, "food": 30, "catering": 35, "mess": 35,
}

MARGIN_RATE = 0.31


# ── Qualification ────────────────────────────────────────────────────────────

def product_match(tender) -> int:
    """0-100: how well the tender's requirement matches our actual products."""
    text = " ".join(filter(None, [
        tender.title or "", tender.required_products or "",
        getattr(tender, "notes", "") or "", tender.department or "",
    ])).lower()
    best = 0
    for kw, score in COFFEE_KEYWORDS.items():
        if kw in text:
            best = max(best, score)
    return best


def document_readiness() -> tuple[int, list]:
    """Share of required documents the founder has confirmed, plus missing list."""
    confirmed = sum(1 for d in DOCUMENT_CHECKLIST if d["status"] == "READY")
    missing = [d["label"] for d in DOCUMENT_CHECKLIST if d["status"] != "READY"]
    pct = round(confirmed / len(DOCUMENT_CHECKLIST) * 100)
    return pct, missing


def qualify_tender(tender) -> dict:
    """
    Compute the full qualification block for a tender from its real fields.
    Returns scores + recommendation; persisting is the caller's job.
    """
    pm = product_match(tender)
    doc_pct, missing_docs = document_readiness()

    value = float(tender.estimated_value or 0)
    margin = round(value * MARGIN_RATE)

    # Deadline pressure
    days = tender.days_to_deadline
    deadline_ok = days is None or days >= 3

    # Win confidence: product fit dominates; document readiness and value
    # realism temper it. No fabricated history — confidence is capped until
    # real outcomes exist to learn from.
    if pm >= 80:
        base = 60
    elif pm >= 45:
        base = 35
    else:
        base = 10
    win_confidence = min(85, base + doc_pct // 4 + (10 if deadline_ok else 0))

    if pm >= 80 and deadline_ok:
        recommendation = "APPLY"
    elif pm >= 45 and deadline_ok:
        recommendation = "REVIEW"
    else:
        recommendation = "SKIP"

    # Revenue opportunity: margin × product fit × confidence, penalised by
    # deadline crunch
    ros = round(margin * (pm / 100) * (win_confidence / 100), 1)
    if days is not None and days < 3:
        ros *= 0.3

    # Bid Effort Score: estimated founder minutes to submit. Base review time
    # + per-missing-document effort + large-tender complexity.
    founder_time_min = 30 + len(missing_docs) * 15 + (30 if value >= 5_000_000 else 0)
    expected_value_rs = margin * (win_confidence / 100)
    margin_per_founder_hour = round(expected_value_rs / (founder_time_min / 60)) if founder_time_min else 0
    if recommendation == "SKIP":
        roi_label = "Skip"
    elif margin_per_founder_hour >= 100_000:
        roi_label = "Excellent ROI"
    elif margin_per_founder_hour >= 25_000:
        roi_label = "Good ROI"
    else:
        roi_label = "Low ROI"

    return {
        "product_match_pct": pm,
        "bid_readiness_pct": doc_pct,
        "win_confidence_pct": win_confidence,
        "revenue_opportunity_score": round(ros, 1),
        "expected_margin_rs": margin,
        "recommended_action": recommendation,
        "missing_documents": missing_docs,
        "deadline_ok": deadline_ok,
        "founder_time_min": founder_time_min,
        "margin_per_founder_hour_rs": margin_per_founder_hour,
        "roi_label": roi_label,
    }


# ── Grounded AI analysis (Cerebras primary, Ollama fallback — both free) ─────

def ai_tender_analysis(tender) -> dict | None:
    """
    Founder review brief generated ONLY from the stored tender record and the
    verified company profile. The prompt forbids invented facts; unknown
    fields are reported as unknown.
    """
    from app.services.llm_client import complete as llm_complete

    tender_facts = {
        "tender_id": tender.tender_id,
        "title": tender.title,
        "department": tender.department,
        "portal": tender.portal,
        "location": tender.location,
        "estimated_value_rs": tender.estimated_value,
        "quantity_kg": tender.quantity_kg,
        "deadline": tender.deadline,
        "days_to_deadline": tender.days_to_deadline,
        "eligibility_check": tender.eligibility_check,
        "required_products": tender.required_products,
        "notes": getattr(tender, "notes", None),
        "source_url": tender.source_url,
    }
    prompt = (
        "You prepare a founder review brief for a government tender bid by an "
        "Indian instant-coffee company.\n\n"
        f"TENDER RECORD (the ONLY tender facts you may use — treat missing "
        f"fields as unknown):\n{json.dumps(tender_facts)}\n\n"
        f"COMPANY (the ONLY claims you may make about the company):\n"
        f"{json.dumps({'products': COMPANY_PROFILE['products'], 'true_claims': COMPANY_PROFILE['true_claims']})}\n\n"
        "STRICT RULES: never invent tender details, past supplies, turnover, "
        "certifications, capacity or references. Where information is missing, "
        "list it under information_needed instead of guessing.\n\n"
        'Reply ONLY with JSON: {"summary": "...", "why_attractive": ["..."], '
        '"risks": ["..."], "information_needed": ["..."], '
        '"suggested_clarifications": ["..."], "bid_strategy": "..."}'
    )
    try:
        content, source = llm_complete(prompt, cerebras_timeout=30.0)
        if not content:
            return None
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            analysis = json.loads(m.group(0))
            analysis["generated_at"] = datetime.utcnow().isoformat()
            analysis["model"] = f"{source}/{'gpt-oss-120b' if source == 'cerebras' else 'llama3.2:3b'}"
            return analysis
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return None


# ── Organization accounts ────────────────────────────────────────────────────

def upsert_organization(db, tender):
    """Every tender's buyer becomes (or updates) a permanent GovOrganization."""
    from app.models.models import GovOrganization
    name = (tender.department or "").strip()
    if not name:
        return None
    org = db.query(GovOrganization).filter(GovOrganization.organization == name).first()
    if not org:
        org = GovOrganization(
            organization=name,
            state=tender.location,
            purchase_portal=tender.portal,
        )
        db.add(org)
        db.flush()
    org.tenders_found = (org.tenders_found or 0) + (0 if tender.organization_id == org.id else 1)
    org.last_tender_date = datetime.utcnow()
    tender.organization_id = org.id
    return org
