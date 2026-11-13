"""e3: e3_hook opener, fit-gate queue order, same-day founder callback."""
from __future__ import annotations

from types import SimpleNamespace as NS

from sqlalchemy.orm import sessionmaker

from app.models.models import ActionQueue, B2BLead, Base
from conftest import memory_engine
from app.services.call_intelligence.variants import opening_text, approved_variants
from app.services.founder_call_pipeline import opening_for, script_discloses
from app.services.festive_priority import fit_gate_rank, coffee_fit_tier


def test_e3_opening_city_disclosure_and_margin_only_for_distributors():
    d = opening_text("e3_hook", "pune", "distributor")
    r = opening_text("e3_hook", "Pune", "supermarket")
    n = opening_text("e3_hook", None, None)
    for t in (d, r, n):
        assert t.startswith("Namaste, main Purity Beans ki AI assistant")
        assert script_discloses(t)[0]
        assert "100%" not in t and "chicory" not in t.lower() and "{" not in t
    assert "Pune mein" in d and ("30% margin" in d or "30% tak margin" in d)
    assert "Pune mein" in r and "margin" not in r and "%" not in r
    assert "margin" not in n and n.split(". ")[1].startswith("Kya aap instant coffee")
    assert "margin" not in opening_text("e3_hook", "Delhi", "retail kirana")
    assert ("30% margin" in opening_text("e3_hook", "Delhi", "Wholesaler") or "30% tak margin" in opening_text("e3_hook", "Delhi", "Wholesaler"))
    lead = NS(city="Jaipur", segment="distributor", business_type=None, contact_name=None, first_name=None)
    assert "Jaipur mein" in opening_for(lead, "e3_hook")


def test_e3_env_approved(monkeypatch):
    monkeypatch.setenv("CALL_OPENER_VARIANTS_APPROVED", "e1_short,e2_stock_check,e3_hook")
    assert "e3_hook" in approved_variants()
    monkeypatch.setenv("CALL_OPENER_VARIANTS_APPROVED", "e1_short,e2_stock_check")
    assert "e3_hook" not in approved_variants()


def test_fit_gate_rank(monkeypatch):
    mk = lambda lvl: NS(coffee_buying_evidence=(f"[INSTANT_COFFEE_FIT 2026-10-08] {lvl} | x" if lvl else ""), qualification_notes="")
    assert [fit_gate_rank(mk(x)) for x in ("CONFIRMED", "LIKELY", None, "UNLIKELY")] == [0, 0, 1, 2]
    assert coffee_fit_tier(mk(None)) == "UNASSESSED"
    monkeypatch.setenv("AI_CALL_FIT_GATE", "0")
    assert fit_gate_rank(mk("UNLIKELY")) == 0


def test_positive_call_creates_sameday_founder_callback():
    from app.api.founder_router import post_ai_call_outcome, AICallOutcomeBody
    eng = memory_engine(); Base.metadata.create_all(eng); db = sessionmaker(bind=eng)()
    lead = B2BLead(company="Fit Distributors", phone="9876500011", segment="distributor")
    db.add(lead); db.commit()
    post_ai_call_outcome(AICallOutcomeBody(lead_id=lead.id, outcome="INTERESTED",
                         summary="wants rates", call_ref="ppp-%d-e3" % lead.id), db)
    items = db.query(ActionQueue).filter_by(lead_id=lead.id, action_type="FOUNDER_CALLBACK_TODAY").all()
    assert len(items) == 1 and items[0].status == "PENDING" and items[0].due_date is not None

    lead2 = B2BLead(company="No Thanks", phone="9876500012", segment="cafe")
    db.add(lead2); db.commit()
    post_ai_call_outcome(AICallOutcomeBody(lead_id=lead2.id, outcome="NOT_INTERESTED",
                         summary="no", call_ref="ppp-%d-e3" % lead2.id), db)
    assert db.query(ActionQueue).filter_by(lead_id=lead2.id).count() == 0
