from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.models import B2BLead
from app.services.smart_outreach import classify_lead, plan_touch, execute_one, run_cycle
from app.services.decision_engine import evaluate_next_action
from app.services import decision_router

router = APIRouter(prefix="/smart-outreach", tags=["Smart Outreach"])


@router.post("/classify/{lead_id}")
def classify(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "lead not found")
    p = classify_lead(db, lead)
    db.commit()
    return {
        "lead_id": lead.id,
        "category": p.category,
        "confidence": p.category_confidence,
        "evidence": p.category_evidence,
        "buying_angle": p.buying_angle,
        "warmth": p.warmth,
        "intent": p.intent,
    }


@router.get("/next/{lead_id}")
def next_action(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "lead not found")
    return evaluate_next_action(lead, db)


@router.post("/decision/{lead_id}")
def decision_preview(lead_id: int, db: Session = Depends(get_db)):
    """Advisory Nemotron decision; never executes the returned action."""
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "lead not found")
    existing = evaluate_next_action(lead, db)
    result = decision_router.decide(
        decision_router.lead_context(lead, existing_next_action=existing)
    )
    return result.to_dict()


@router.post("/run/{lead_id}")
def run_one(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "lead not found")
    return execute_one(db, lead)


@router.post("/run")
def run(limit: int = 20, db: Session = Depends(get_db)):
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be 1..100")
    return run_cycle(db, limit=limit)
