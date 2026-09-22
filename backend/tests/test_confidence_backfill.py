"""Fair confidence backfill — evidence-based, not invented."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, B2BLead
from app.services import trust_promoter as tp
from app.services import trust_confidence as tc
from conftest import memory_engine


@pytest.fixture
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()
    eng.dispose()


def test_live_tech_raises_website_to_floor(db):
    lead = B2BLead(
        company="Abohar Coffee House", city="Abohar",
        email="owner@abohorcoffeehouse.in", email_source="WEBSITE",
        email_trust="VERIFIED", email_confidence=0, division="RETAIL",
    )
    db.add(lead)
    db.commit()
    # Simulate a successful verify that stashed tech but never wrote confidence
    lead._tech = {"syntax_ok": True, "mx_ok": True, "smtp_ok": True, "status": "VALID"}
    out = tc.sync_stored_confidence(lead, db)
    db.commit()
    db.refresh(lead)
    assert out["wrote"] is True
    assert (lead.email_confidence or 0) >= tp.CONFIDENCE_FLOOR
    ok, why = tp.may_send(lead)
    assert ok is True, why


def test_no_evidence_does_not_invent_confidence(db):
    lead = B2BLead(
        company="Unknown Co", city="X",
        email="maybe@unknown.example", email_source="GUESSED",
        email_trust="VERIFIED", email_confidence=0, division="RETAIL",
    )
    db.add(lead)
    db.commit()
    out = tc.sync_stored_confidence(lead, db)
    assert (out["confidence"] or 0) < tp.CONFIDENCE_FLOOR
    assert tp.may_send(lead)[0] is False
