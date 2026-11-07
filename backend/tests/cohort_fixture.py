"""Admit fixture leads to the cohort for tests that still need a candidate."""
from __future__ import annotations

import json
from datetime import datetime


def authorize_call(db, lead):
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="CALL_AUTHORIZATION_RECORDED",
        actor="TEST",
        channel="call",
        payload=json.dumps({
            "source": "CALL_AUTHORIZATION",
            "consent_phone": lead.phone,
            "evidence_sha256": "a" * 64,
        }),
        occurred_at=datetime.utcnow(),
    ))
    db.commit()


def admit(monkeypatch, db, leads, *, calls=False):
    from app.services import outbound_cohort as cohort
    leads = list(leads)
    monkeypatch.setattr(cohort, "allow_list_ids", lambda: frozenset(lead.id for lead in leads))
    if calls:
        for lead in leads:
            authorize_call(db, lead)
