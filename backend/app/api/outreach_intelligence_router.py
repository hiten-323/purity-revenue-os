"""HTTP API for Outreach Intelligence V1 — dry/admin only; never sends or dials."""
from __future__ import annotations
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database.database import get_db

router = APIRouter(prefix="/outreach-intelligence", tags=["Outreach Intelligence"])


class CorrectionBody(BaseModel):
    lead_id: int
    field: str
    new_value: str
    old_value: Optional[str] = None
    event_id: Optional[int] = None
    note: Optional[str] = None
    corrected_by: str = "FOUNDER"
    # Call learning loop: correct a structured call result. field is one of
    # call_intelligence.corrections.CORRECTABLE_FIELDS (or "call_result.<f>").
    call_result_id: Optional[int] = None


class RecalculateBody(BaseModel):
    category: Optional[str] = None
    dry_run: bool = Field(default=True, description="V1 always recalculates stats only; never sends.")


@router.post("/corrections")
def create_correction(body: CorrectionBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.outreach_intelligence.learning_loop import apply_correction
    old_value = body.old_value
    call_result = None
    if body.call_result_id is not None:
        from app.services.call_intelligence.corrections import apply_call_result_correction
        try:
            call_result, prev = apply_call_result_correction(
                db, call_result_id=body.call_result_id, field=body.field,
                new_value=body.new_value, lead_id=body.lead_id)
        except LookupError as e:
            db.rollback()
            raise HTTPException(404, str(e))
        except ValueError as e:
            db.rollback()
            raise HTTPException(400, str(e))
        old_value = old_value if old_value is not None else prev
    row = apply_correction(
        db, lead_id=body.lead_id, field=body.field, new_value=body.new_value,
        old_value=old_value, event_id=body.event_id, note=body.note,
        corrected_by=body.corrected_by,
    )
    db.commit()
    extra = {}
    if call_result is not None:
        extra = {"call_result_id": call_result.id, "call_result_confidence": call_result.confidence}
    return {
        **extra,
        "id": row.id, "lead_id": row.lead_id, "field": row.field,
        "old_value": row.old_value, "new_value": row.new_value,
        "corrected_by": row.corrected_by,
        "corrected_at": row.corrected_at.isoformat() if row.corrected_at else None,
    }


@router.get("/report")
def report(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.outreach_intelligence.report import build_intelligence_report
    return build_intelligence_report(db)


@router.get("/lead/{lead_id}/profile")
def lead_profile(lead_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.models.models import B2BLead
    from app.services.outreach_intelligence.experience_store import build_lead_outreach_profile
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "lead not found")
    return build_lead_outreach_profile(db, lead)


@router.post("/recalculate")
def recalculate(body: RecalculateBody = RecalculateBody(), db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.outreach_intelligence.learning_loop import recalculate_segment_stats
    result = recalculate_segment_stats(db, category=body.category)
    result["dry_run"] = True
    result["sent"] = False
    result["dialled"] = False
    return result


@router.post("/sync")
def sync_ledger(limit: int = 500, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.outreach_intelligence.event_ledger import sync_from_existing
    return sync_from_existing(db, limit=limit)


# ── call learning loop (call_intelligence) ──────────────────────────────────
# Read endpoints are advisory. The reconcile POST defaults to dry_run and is
# admin-only; it never dials and never changes outreach_stage.

def _admin(request: Request) -> None:
    from app.api.auth import require_api_admin
    require_api_admin(request)


@router.get("/calls")
def calls_report(days: int = 30, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.call_intelligence.report import build_calls_report
    return build_calls_report(db, days=min(max(days, 1), 365))


@router.get("/lead/{lead_id}/call-brief", dependencies=[Depends(_admin)])
def lead_call_brief(lead_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.models.models import B2BLead
    from app.services.call_intelligence.brief import build_call_brief
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "lead not found")
    brief = build_call_brief(db, lead)
    brief["dialled"] = False
    return brief


class ReconcileBody(BaseModel):
    dry_run: bool = True
    older_than_minutes: Optional[int] = None
    limit: Optional[int] = None


@router.post("/calls/reconcile", dependencies=[Depends(_admin)])
def calls_reconcile(body: ReconcileBody = ReconcileBody(), db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.call_intelligence.reconciler import reconcile_stuck_calls
    res = reconcile_stuck_calls(db, dry_run=body.dry_run,
                                older_than_minutes=body.older_than_minutes, limit=body.limit)
    if body.dry_run:
        db.rollback()
    res["dialled"] = False
    return res
