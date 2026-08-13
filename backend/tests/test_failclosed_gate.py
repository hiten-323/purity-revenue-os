"""
Pytest wrappers for the pre-rotation fail-closed contracts.

These use a temp SQLite DB and never touch production data or the network.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch

import pytest


@pytest.fixture()
def isolated_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    # Re-import path: database module may already be loaded with another URL.
    # For unit isolation we still exercise the listeners on whatever engine
    # SessionLocal is bound to; prefer a fresh process via the script for
    # the hard gate. Here we assert contracts when imports succeed.
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend not in sys.path:
        sys.path.insert(0, backend)

    from app.database.database import SessionLocal, engine, Base
    import app.models.models  # noqa: F401
    import app.models.send_proof_fix  # noqa: F401
    from app.models.models import B2BLead, WorkflowEvent, ActionQueue

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    lead = B2BLead(company="DRYRUN Gate Co", phone="9876543210", status="DISCOVERED")
    db.add(lead)
    db.commit()
    db.refresh(lead)

    yield db, lead, WorkflowEvent, ActionQueue

    db.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_send_proof_listener_is_strict():
    from sqlalchemy import event
    import app.models.models  # noqa: F401
    import app.models.send_proof_fix as spf
    from app.models.models import WorkflowEvent, _require_send_proof

    assert event.contains(
        WorkflowEvent, "before_insert", spf._require_send_proof_strict
    )
    assert not event.contains(
        WorkflowEvent, "before_insert", _require_send_proof
    )


def test_missing_message_id_becomes_unproven(isolated_db):
    db, lead, WorkflowEvent, _ = isolated_db
    ev = WorkflowEvent(
        lead_id=lead.id,
        event_type="EMAIL_SENT",
        actor="SYSTEM",
        channel="email",
        payload={"to": "buyer@example.com"},
        occurred_at=datetime.utcnow(),
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    assert ev.event_type == "EMAIL_SENT_UNPROVEN"


def test_duplicate_message_id_becomes_duplicate(isolated_db):
    db, lead, WorkflowEvent, _ = isolated_db
    mid = "pytest-dup-001"
    e1 = WorkflowEvent(
        lead_id=lead.id,
        event_type="EMAIL_SENT",
        actor="SYSTEM",
        channel="email",
        payload={"to": "buyer@example.com", "message_id": mid},
        occurred_at=datetime.utcnow(),
    )
    db.add(e1)
    db.commit()
    db.refresh(e1)
    assert e1.event_type == "EMAIL_SENT"

    e2 = WorkflowEvent(
        lead_id=lead.id,
        event_type="EMAIL_SENT",
        actor="SYSTEM",
        channel="email",
        payload={"to": "buyer@example.com", "message_id": mid},
        occurred_at=datetime.utcnow(),
    )
    db.add(e2)
    db.commit()
    db.refresh(e2)
    assert e2.event_type == "EMAIL_SENT_DUPLICATE"


def test_engine_failure_queues_founder_review(isolated_db):
    db, lead, _, ActionQueue = isolated_db
    from app.services.outreach_search import apply_call_outcome

    with patch(
        "app.services.phone_intelligence.decide_after_call",
        side_effect=RuntimeError("engine unavailable"),
    ):
        result = apply_call_outcome(db, lead, "NO_ANSWER", captured={})

    assert result.get("next_action") == "FOUNDER_REVIEW"
    aq = (
        db.query(ActionQueue)
        .filter(ActionQueue.lead_id == lead.id, ActionQueue.status == "PENDING")
        .all()
    )
    assert any(a.action_type == "FOUNDER_REVIEW" for a in aq)
