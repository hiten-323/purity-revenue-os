"""HTTP API for Outreach Intelligence V1 — dry/admin only; never sends or dials."""
from __future__ import annotations
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
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


class RecalculateBody(BaseModel):
    category: Optional[str] = None
    dry_run: bool = Field(default=True, description="V1 always recalculates stats only; never sends.")


@router.post("/corrections")
def create_correction(body: CorrectionBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.outreach_intelligence.learning_loop import apply_correction
    row = apply_correction(
        db, lead_id=body.lead_id, field=body.field, new_value=body.new_value,
        old_value=body.old_value, event_id=body.event_id, note=body.note,
        corrected_by=body.corrected_by,
    )
    db.commit()
    return {
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
