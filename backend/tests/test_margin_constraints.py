"""14 Oct constraints: distributor-only 30/35% margin, no 100%, no blanket no-chicory, 35% logged."""
from __future__ import annotations
from sqlalchemy.orm import sessionmaker
from app.models.models import B2BLead, Base, WorkflowEvent
from conftest import memory_engine
from app.services import founder_call_pipeline as p


def test_constraints_text():
    t = " ".join(p.CALL_CONSTRAINTS)
    assert "100% coffee" not in t and "zero chicory" not in t.lower()
    assert "30%" in t and "35% maximum" in t and "Never above 35%" in t
    assert "Ultra Blend" in t and "70% coffee and 30% chicory" in t
    assert "supermarkets, retail" in t


def test_35_offer_is_logged():
    from app.api.founder_router import post_ai_call_outcome, AICallOutcomeBody
    eng = memory_engine(); Base.metadata.create_all(eng); db = sessionmaker(bind=eng)()
    lead = B2BLead(company="D Traders", phone="9876500021", segment="distributor")
    db.add(lead); db.commit()
    turns = [{"role": "assistant", "text": "Cash payment pe hum 35% tak margin de sakte hain", "turn_index": 0},
             {"role": "user", "text": "theek hai", "turn_index": 1}]
    post_ai_call_outcome(AICallOutcomeBody(lead_id=lead.id, outcome="INTERESTED", summary="ok",
                         call_ref="ppp-%d-m" % lead.id, turns=turns), db)
    assert db.query(WorkflowEvent).filter_by(lead_id=lead.id, event_type="MARGIN_35_OFFERED").count() == 1
