"""
Clearing a do-not-call flag is not the same as being given permission.

override_lead_dnc used to do both: it cleared do_not_call AND wrote
consent_status="EXPLICIT", consent_source="FOUNDER_OVERRIDE". That produced a
record indistinguishable from a business actually agreeing to be contacted, on
an endpoint with no authentication, which then satisfied every downstream
consent gate — whatsapp_sender, calling_agent, and the Nuraveda adapter all
read consent_status and would have believed it.

It also replaced dnc_reason with the literal string "Override", erasing the
only record of why the person asked not to be called.

An override says "we think this flag was set by mistake". Consent says "they
told us we may contact them". Only the business can supply the second.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, B2BLead, WorkflowEvent
from conftest import memory_engine


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def _call(db, lead_id, reason="Marked DNC by a mis-parsed call outcome on 3 Sept",
          by="Hiten Jain"):
    from app.api.endpoints import DNCOverrideRequest, override_lead_dnc
    return override_lead_dnc(lead_id, DNCOverrideRequest(reason=reason, override_by=by), db)


def _lead(db, **kw):
    lead = B2BLead(company=kw.pop("company", "Test Traders"), city="Delhi",
                   phone="+91-98765-43210", do_not_call=True,
                   dnc_reason=kw.pop("dnc_reason", "asked us to stop, 12 Aug"), **kw)
    db.add(lead)
    db.commit()
    return lead


def test_override_does_not_create_consent(db):
    """The regression that mattered. Downstream gates read consent_status and
    cannot tell a manufactured EXPLICIT from a real one."""
    lead = _lead(db, consent_status="UNKNOWN")
    _call(db, lead.id)
    db.refresh(lead)

    assert lead.do_not_call is False, "the override should still clear the flag"
    assert (lead.consent_status or "UNKNOWN").upper() == "UNKNOWN", (
        f"override manufactured consent: {lead.consent_status}")


def test_original_dnc_reason_survives(db):
    """Why someone asked not to be called is the one thing worth keeping."""
    lead = _lead(db, dnc_reason="asked us to stop, 12 Aug")
    _call(db, lead.id)
    db.refresh(lead)

    assert "asked us to stop, 12 Aug" in (lead.dnc_reason or ""), (
        f"original reason destroyed: {lead.dnc_reason}")


def test_override_is_audited(db):
    lead = _lead(db)
    _call(db, lead.id, reason="Flag set by a mis-parsed call outcome, verified with the owner")
    evs = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type == "DNC_OVERRIDE").all()
    assert evs, "no DNC_OVERRIDE event written"
    payload = evs[0].payload or {}
    assert payload.get("was_do_not_call") is True
    assert "mis-parsed" in str(payload.get("override_reason", ""))


def test_a_vague_reason_is_refused(db):
    """'Override' is what the old code wrote automatically. If a one-word
    reason is accepted, the audit trail says nothing."""
    from fastapi import HTTPException
    lead = _lead(db)
    for bad in ("", "ok", "Override", "   "):
        with pytest.raises(HTTPException) as exc:
            _call(db, lead.id, reason=bad)
        assert exc.value.status_code == 400
    db.refresh(lead)
    assert lead.do_not_call is True, "a refused override still cleared the flag"


def test_existing_real_consent_is_left_alone(db):
    """The endpoint must not touch consent in either direction — not grant it,
    and not clobber a genuine grant either."""
    lead = _lead(db, consent_status="EXPLICIT", consent_source="FOUNDER_CALL")
    _call(db, lead.id)
    db.refresh(lead)
    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_source == "FOUNDER_CALL", "real consent provenance was overwritten"
