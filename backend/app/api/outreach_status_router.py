"""Read-only production Auto-Outreach status API.

This route only reads the OutreachEvent ledger. It never sends, dials,
changes flags, writes CRM state, or mutates the database.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.database import get_db

router = APIRouter(prefix="/outreach", tags=["Auto-Outreach Status"])


@router.get("/status")
def outreach_status(
    hours: int = Query(default=1, ge=1, le=168),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from app.services.outreach_intelligence.status_report import build_status_report

    report = build_status_report(db, hours=hours)
    report["api"] = {
        "read_only": True,
        "endpoint": "/api/v1/outreach/status",
        "hours": hours,
    }
    return report
