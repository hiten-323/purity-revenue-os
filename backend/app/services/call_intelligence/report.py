"""Calls section of the outreach-intelligence report + founder follow-up list."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.services.call_intelligence import lessons as L
from app.services.call_intelligence.models import CallResult, results_table_ready
from app.services.call_intelligence.taxonomy import FOLLOW_UP_OUTCOMES, RESULT_OUTCOMES


def _mask(phone: str | None) -> str | None:
    d = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"{'x' * max(0, len(d) - 4)}{d[-4:]}" if d else None


def build_calls_report(db, *, days: int = 30, follow_up_limit: int = 100,
                       now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    out: dict[str, Any] = {"window_days": days, "generated_at_utc": now.isoformat()}
    try:
        stuck = db.execute(text(
            "SELECT COUNT(*) FROM b2b_leads WHERE call_status = 'CALLING'")).scalar() or 0
    except Exception:  # noqa: BLE001
        stuck = None
    out["leads_still_calling"] = stuck
    if not results_table_ready(db):
        out["error"] = "call_results table unavailable"
        return out

    since = now - timedelta(days=max(1, days))
    rows = (db.query(CallResult)
            .filter((CallResult.started_at >= since) | (CallResult.started_at.is_(None)))
            .all())
    final = [r for r in rows if r.status == "FINAL"]
    breakdown = Counter(r.outcome or "UNKNOWN" for r in final)
    out["outcome_breakdown"] = {k: breakdown.get(k, 0) for k in RESULT_OUTCOMES}
    out["by_confidence"] = dict(Counter(r.confidence or "NONE" for r in final))
    out["by_source"] = dict(Counter(r.source or "NONE" for r in final))
    out["open_attempts"] = sum(1 for r in rows if r.status == "OPEN")
    out["reconciled_unknown"] = sum(1 for r in final if r.source == "reconciler"
                                    and (r.outcome or "") == "UNKNOWN")

    agg = L.aggregate(db, days=days, now=now)
    out["funnel"] = agg["overall"]
    out["lessons"] = agg["lessons"]
    out["thresholds"] = agg["thresholds"]
    out["by_segment"] = {k: agg["by"][k] for k in ("business_type", "hour_bucket",
                                                   "language", "opener_variant")}

    from app.models.models import B2BLead
    fu = [r for r in final if (r.outcome or "") in FOLLOW_UP_OUTCOMES]
    fu.sort(key=lambda r: (r.next_action_at or datetime.max, -(r.id or 0)))
    leads = {l.id: l for l in db.query(B2BLead).filter(
        B2BLead.id.in_([r.lead_id for r in fu[:follow_up_limit]] or [-1])).all()}
    seen: set[int] = set()
    items = []
    for r in fu:
        if r.lead_id in seen:
            continue
        seen.add(r.lead_id)
        lead = leads.get(r.lead_id)
        items.append({
            "lead_id": r.lead_id,
            "company": getattr(lead, "company", None),
            "city": getattr(lead, "city", None),
            "phone_masked": _mask(getattr(lead, "phone", None)),
            "outcome": r.outcome, "next_action": r.next_action,
            "next_action_at_utc": r.next_action_at.isoformat() if r.next_action_at else None,
            "callback_window": getattr(lead, "founder_callback_window", None),
            "summary": r.summary, "objections": r.objections or [],
            "reached_decision_maker": r.reached_decision_maker,
            "call_at_utc": (r.ended_at or r.started_at).isoformat() if (r.ended_at or r.started_at) else None,
            "confidence": r.confidence, "call_result_id": r.id,
        })
        if len(items) >= follow_up_limit:
            break
    out["founder_follow_up"] = items
    out["safety"] = {"advisory_only": True, "does_not_dial": True, "does_not_send": True}
    return out
