"""Human correction of a CallResult, via the PR #33 corrections API.

POST /api/v1/outreach-intelligence/corrections with ``call_result_id`` and
``field`` = one of CORRECTABLE_FIELDS (optionally prefixed ``call_result.``).
The OutreachOutcomeCorrection audit row is still written by apply_correction;
this module applies the change to the call_results row, marks it
HUMAN_CORRECTED (which outranks every automatic source) and re-feeds the
ledger. Corrections can only *withdraw* contact permission (DO_NOT_CALL sets
do_not_call); nothing here grants any.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.services.call_intelligence.models import CallResult
from app.services.call_intelligence.taxonomy import RESULT_OUTCOMES

CORRECTABLE_FIELDS = {
    "outcome", "connected", "reached_decision_maker", "objections", "language",
    "sentiment", "rude_or_complaint", "next_action", "next_action_at", "summary",
    "drop_off_stage",
}
_BOOL = {"connected", "reached_decision_maker", "rude_or_complaint"}


def normalise_field(field: str) -> str:
    f = (field or "").strip()
    return f.split(".", 1)[1] if f.startswith("call_result.") else f


def _coerce(field: str, value: str):
    v = (value or "").strip()
    if field in _BOOL:
        if v.lower() in {"1", "true", "yes", "y"}:
            return True
        if v.lower() in {"0", "false", "no", "n"}:
            return False
        if v.lower() in {"", "none", "null", "unknown"}:
            return None
        raise ValueError(f"{field} expects true/false/unknown, got {value!r}")
    if field == "outcome":
        u = v.upper()
        if u not in RESULT_OUTCOMES:
            raise ValueError(f"outcome must be one of {', '.join(RESULT_OUTCOMES)}")
        return u
    if field == "objections":
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return [str(x).upper() for x in parsed]
        except ValueError:
            pass
        return [x.strip().upper() for x in v.split(",") if x.strip()]
    if field == "next_action_at":
        return datetime.fromisoformat(v.replace("Z", "")) if v else None
    return v or None


def apply_call_result_correction(db, *, call_result_id: int, field: str, new_value: str,
                                 lead_id: int | None = None) -> tuple[CallResult, str | None]:
    f = normalise_field(field)
    if f not in CORRECTABLE_FIELDS:
        raise ValueError(f"field {field!r} is not correctable; expected one of "
                         + ", ".join(sorted(CORRECTABLE_FIELDS)))
    row = db.query(CallResult).filter(CallResult.id == call_result_id).first()
    if row is None:
        raise LookupError(f"call_result {call_result_id} not found")
    if lead_id is not None and row.lead_id != lead_id:
        raise ValueError("call_result does not belong to this lead")
    old = getattr(row, f)
    setattr(row, f, _coerce(f, new_value))
    row.confidence = "HUMAN_CORRECTED"
    row.source = "founder"
    row.updated_at = datetime.utcnow()
    ev = dict(row.evidence or {})
    ev.setdefault("corrections", []).append({"field": f, "old": str(old), "new": new_value})
    row.evidence = ev
    if f == "outcome" and row.outcome == "DO_NOT_CALL":
        from app.models.models import B2BLead
        lead = db.query(B2BLead).filter(B2BLead.id == row.lead_id).first()
        if lead is not None and hasattr(lead, "do_not_call"):
            lead.do_not_call = True
    db.flush()
    try:
        from app.models.models import B2BLead
        from app.services.call_intelligence.capture import feed_ledger
        # Audit event only: the original observation was already counted in
        # the PR #33 aggregates; call lessons re-read call_results directly.
        feed_ledger(db, row, db.query(B2BLead).filter(B2BLead.id == row.lead_id).first(),
                    update_aggregates=False)
    except Exception:  # noqa: BLE001
        pass
    return row, (None if old is None else str(old))
