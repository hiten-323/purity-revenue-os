"""Experience store + Lead Outreach Profile. Advisory only; wraps build_learning_context."""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.services.outreach_intelligence.models import OutreachExperienceAggregate

try:
    from app.services.outreach_learning import MIN_EVIDENCE
except Exception:  # noqa: BLE001
    MIN_EVIDENCE = 5


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float | None:
    if n <= 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / denom)


def proportion_with_confidence(wins: int, n: int) -> dict[str, Any]:
    if n <= 0:
        return {"rate": None, "wins": 0, "sample_size": 0, "wilson_lb": None,
                "promoted": False, "min_evidence": MIN_EVIDENCE}
    return {
        "rate": wins / n, "wins": wins, "sample_size": n,
        "wilson_lb": wilson_lower_bound(wins, n),
        "promoted": n >= MIN_EVIDENCE, "min_evidence": MIN_EVIDENCE,
    }


def _segment_key(category: str | None, geo: str | None = None) -> str:
    cat = (category or "UNKNOWN").upper()
    geo_part = (geo or "").strip().upper()
    return f"{cat}|{geo_part}" if geo_part else cat


def retrieve_similar_lead_experience(
    db: Session, *, category: str | None, geo: str | None = None, channel: str | None = None,
) -> dict[str, Any]:
    key = _segment_key(category, geo)
    q = db.query(OutreachExperienceAggregate).filter(OutreachExperienceAggregate.segment_key == key)
    if channel:
        q = q.filter(OutreachExperienceAggregate.channel == channel)
    rows = q.all()
    if not rows and geo:
        key = _segment_key(category, None)
        q = db.query(OutreachExperienceAggregate).filter(OutreachExperienceAggregate.segment_key == key)
        if channel:
            q = q.filter(OutreachExperienceAggregate.channel == channel)
        rows = q.all()
    metrics = {}
    for row in rows:
        metrics[row.metric] = {
            "value": row.value, "sample_size": row.sample_size, "wins": row.wins,
            "channel": row.channel, "promoted": (row.sample_size or 0) >= MIN_EVIDENCE,
            "wilson_lb": wilson_lower_bound(row.wins or 0, row.sample_size or 0),
        }
    return {
        "segment_key": key, "channel": channel, "metrics": metrics,
        "min_evidence": MIN_EVIDENCE,
        "has_promoted_evidence": any(m.get("promoted") for m in metrics.values()),
    }


def build_lead_outreach_profile(db: Session, lead) -> dict[str, Any]:
    profile = None
    category = "UNKNOWN"
    try:
        from app.services.smart_outreach import classify_lead
        profile = classify_lead(db, lead)
        category = (getattr(profile, "category", None) or "UNKNOWN").upper()
    except Exception:  # noqa: BLE001
        category = str(getattr(lead, "division", None) or getattr(lead, "segment", None) or "UNKNOWN").upper()

    learning: dict[str, Any] = {}
    try:
        from app.services.outreach_learning import build_learning_context
        learning = build_learning_context(db, lead, profile) or {}
    except Exception as exc:  # noqa: BLE001
        learning = {"summary": f"learning unavailable: {exc.__class__.__name__}", "error": True}

    geo = getattr(lead, "city", None)
    email_xp = retrieve_similar_lead_experience(db, category=category, geo=geo, channel="email")
    call_xp = retrieve_similar_lead_experience(db, category=category, geo=geo, channel="call")
    evidence_count = int(learning.get("evidence_count") or 0)
    confidence = "HIGH" if evidence_count >= MIN_EVIDENCE else ("MEDIUM" if evidence_count > 0 else "LOW")

    products = {
        "CAFE": ["espresso_beans", "filter_blend"], "HOTEL": ["filter_blend", "premium_arabica"],
        "RESTAURANT": ["filter_blend"], "DISTRIBUTOR": ["bulk_arabica", "bulk_robusta", "private_label"],
        "WHOLESALER": ["bulk_arabica", "bulk_robusta"], "CORPORATE": ["office_pantry_kit"],
        "GIFTING": ["gift_hamper"], "PRIVATE_LABEL": ["private_label"],
    }
    objection = learning.get("top_objection")
    result = {
        "lead_id": getattr(lead, "id", None),
        "business_type": category,
        "location": geo,
        "similar_lead_experience": {
            "email": email_xp, "call": call_xp,
            "learning_summary": learning.get("summary"),
            "comparable_leads": learning.get("comparable_leads"),
        },
        "recommended_email_angle": learning.get("learned_intent") or category or "DISCOVERY",
        "recommended_call_opening": learning.get("recommended_call_question"),
        "recommended_products": products.get(category, ["filter_blend"]),
        "likely_objections": [objection] if objection else [],
        "recommended_cta": learning.get("recommended_cta") or "catalogue",
        "channels": {
            "email": {"advisory": True},
            "call": {"advisory": True},
            "selected": None,
            "note": "BOTH channels remain independently eligible; profile does not pick one.",
        },
        "confidence": confidence,
        "evidence": {
            "evidence_count": evidence_count,
            "min_evidence": MIN_EVIDENCE,
            "promoted": evidence_count >= MIN_EVIDENCE,
            "email_metrics": email_xp.get("metrics"),
            "call_metrics": call_xp.get("metrics"),
        },
    }
    for banned in ("eligible", "eligibility", "suppress", "suppressed", "may_send", "may_call", "best_channel"):
        result.pop(banned, None)
    return result
