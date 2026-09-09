"""
The trust sweep is a maintenance job, not a decision engine.

worker.py calls trust_promoter.run() every TRUST_SWEEP_HOURS. That job is
allowed to reconcile evidence already on record into email_trust and to write
an audit event. It is allowed to do nothing else.

These tests pin that boundary. A comment saying "this never sends" does not
survive a future edit; an assert does. They also pin the two rules that decide
how much the send pool can actually grow:

  * published-on-own-site evidence promotes VALIDATED -> VERIFIED
  * a shared/chain inbox does NOT, however good the evidence is, because one
    inbox reaching 24 branches is one relationship, not 24 send permissions

That second rule is the reason the sweep yields ~2 sendable leads and not ~41.
If someone "fixes" it, this test fails, which is the point.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, B2BLead, WorkflowEvent
from app.services import trust_promoter as tp
from conftest import memory_engine

# Every field the sweep is permitted to write.
ALLOWED = {"email_trust", "email_verified", "email_source", "email_trust_at",
           "email_confidence"}

# Fields owned by other authorities: cadence belongs to sequence_engine,
# offers to the pricing gates, scores to the learning layer.
FORBIDDEN = ["email_sequence_stage", "email_sequence_last_sent",
             "next_followup_date", "stage_entered_date",
             "proposal_suggested_price", "proposal_discount_percent",
             "discount_given", "score", "intent_score", "final_score",
             "lead_temperature_score", "coffee_buying_score",
             "searched_category", "status"]


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = memory_engine()
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()
    eng.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def _lead(db, company, email, source="WEBSITE", trust="VALIDATED"):
    lead = B2BLead(company=company, city="Abohar", email=email,
                   email_source=source, email_trust=trust, division="RETAIL",
                   email_sequence_stage=2, score=55, status="CONTACTED")
    db.add(lead)
    db.commit()
    return lead


def _snapshot(lead):
    return {f: getattr(lead, f) for f in FORBIDDEN}


def test_website_evidence_promotes_to_verified(db):
    """The whole reason the sweep exists: evidence on record, never applied."""
    lead = _lead(db, "Abohar Coffee House", "owner@abohorcoffeehouse.in")
    assert tp.may_send(lead)[0] is False, "precondition: VALIDATED cannot send"

    tp.run(db, verify=False)
    db.refresh(lead)

    assert lead.email_trust == "VERIFIED", f"stuck at {lead.email_trust}"


def test_promotion_alone_does_not_open_sending(db):
    """Sending is gated TWICE: trust grants permission, confidence earns it.

    Measured on live data: all four leads the sweep promotes to VERIFIED land
    at confidence 0 and stay unsendable, because confidence is derived from
    interaction history and a cold address has none. That is why the sweep is
    hygiene and not a lead-supply fix — it reconciles state, it does not
    manufacture standing to contact anyone.

    If someone lowers CONFIDENCE_FLOOR or seeds confidence at promotion time to
    "unblock the pipeline", this test fails. It should.
    """
    lead = _lead(db, "Ferozepur Coffee Co", "owner@ferozepurcoffee.in")
    tp.run(db, verify=False)
    db.refresh(lead)

    assert lead.email_trust == "VERIFIED"
    assert (lead.email_confidence or 0) < tp.CONFIDENCE_FLOOR, (
        f"a cold lead was promoted with confidence {lead.email_confidence} — "
        f"confidence must come from interaction, not from being promoted")

    ok, why = tp.may_send(lead)
    assert ok is False, "VERIFIED alone opened sending; the floor is not holding"
    assert "confidence" in why, f"blocked for the wrong reason: {why}"


def test_confidence_floor_is_the_only_remaining_gate(db):
    """The mirror of the test above: once real interaction has earned the
    confidence, the same lead sends. Proves the floor blocks, not the trust
    state, so the two gates are not silently the same gate."""
    lead = _lead(db, "Sriganganagar Roastery", "owner@sgnrroastery.in")
    tp.run(db, verify=False)
    db.refresh(lead)
    assert tp.may_send(lead)[0] is False

    lead.email_confidence = tp.CONFIDENCE_FLOOR
    db.commit()

    ok, why = tp.may_send(lead)
    assert ok is True, f"VERIFIED at the floor still refused: {why}"


def test_shared_inbox_is_not_promoted(db):
    """One inbox across four branches is ONE relationship. The cap must hold
    even though every branch carries the same WEBSITE evidence."""
    shared = "hello@examplechain.in"
    branches = [_lead(db, f"Example Chain Branch {n}", shared) for n in range(4)]

    tp.run(db, verify=False)

    for b in branches:
        db.refresh(b)
        assert b.email_trust == "VALIDATED", (
            f"{b.company} promoted to {b.email_trust} — a chain inbox became "
            f"4 send permissions")
        assert tp.may_send(b)[0] is False


def test_sweep_writes_nothing_outside_the_trust_columns(db):
    """No sending, no classification, no cadence, no offer, no learning."""
    lead = _lead(db, "Punjab Roasters", "contact@punjabroasters.in")
    before = _snapshot(lead)

    tp.run(db, verify=False)
    db.refresh(lead)

    assert lead.email_trust == "VERIFIED", "sweep did not actually run"
    for field, was in before.items():
        assert getattr(lead, field) == was, (
            f"sweep wrote {field}: {was!r} -> {getattr(lead, field)!r}")

    kinds = {e.event_type for e in db.query(WorkflowEvent).all()}
    assert kinds <= {"TRUST_TRANSITION"}, f"sweep emitted {kinds - {'TRUST_TRANSITION'}}"
    for banned in ("EMAIL_SENT", "EMAIL_SENT_UNPROVEN", "WHATSAPP_SENT",
                   "EMAIL_DRAFTED", "CALL_PLACED"):
        assert banned not in kinds, f"the maintenance sweep produced {banned}"


def test_sweep_is_a_fixpoint(db):
    """Documented as safe to run repeatedly. The worker runs it on a timer, so
    a second pass must be a no-op — otherwise it is an accumulator and every
    cycle would re-write history."""
    _lead(db, "Malout Cafe", "info@maloutcafe.in")
    _lead(db, "Fazilka Foods", "buying@fazilkafoods.in")

    first = tp.run(db, verify=False)
    assert first["moved"] > 0, "nothing moved; the test proves nothing"

    second = tp.run(db, verify=False)
    assert second["moved"] == 0, f"second pass moved {second['moved']} — not a fixpoint"
    assert not second["errors"]

    events = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "TRUST_TRANSITION").count()
    assert events == first["moved"], "re-running duplicated audit history"
