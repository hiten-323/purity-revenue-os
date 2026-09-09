"""
The Scrapling harvester must not become a third trust authority.

As merged, harvest() called contact_trust.grant(), which assigns email_trust
directly and never consults trust_promoter._is_shared_inbox. A chain inbox
published on its own site — info@more.in on more.in — would therefore be granted
VERIFIED on every branch lead, producing N send permissions where the cap says
one relationship. It also assigned whatsapp_number from any discovered phone,
including STD landlines that WhatsApp cannot reach.

These tests pin both. They exercise harvest() directly with a stubbed fetch, so
no network is touched.
"""
from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, B2BLead, WorkflowEvent
from app.services import scrapling_harvester as sh
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


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("SCRAPLING_ENABLED", "1")
    monkeypatch.setenv("SCRAPLING_ALLOW_STEALTH", "0")


def _stub(monkeypatch, emails, phones, whatsapp=""):
    """Replace the network fetch. harvest_one is the only I/O boundary."""
    def fake(website):
        return {"website": website, "emails": list(emails), "phones": list(phones),
                "whatsapp": whatsapp, "pages": [{"url": website}], "error": ""}
    monkeypatch.setattr(sh, "harvest_one", fake)


def _branch(db, n, site="https://more.in"):
    lead = B2BLead(company=f"More Supermarket {n}", city="Ludhiana",
                   website=site, email="", phone="", division="RETAIL")
    db.add(lead)
    db.commit()
    return lead


def test_chain_inbox_does_not_become_a_send_permission_per_branch(db, monkeypatch):
    """The regression that mattered. One inbox across four branches is ONE
    relationship. Every branch must be capped, not promoted."""
    branches = [_branch(db, i) for i in range(4)]
    _stub(monkeypatch, ["info@more.in"], [])

    sh.harvest(db, limit=10, only_missing=True)

    for b in branches:
        db.refresh(b)
        assert b.email == "info@more.in"
        assert b.email_trust != "VERIFIED", (
            f"{b.company} was granted {b.email_trust} on a shared chain inbox — "
            f"that is a send permission per branch")


def test_trust_is_written_by_the_engine_not_the_harvester(db, monkeypatch):
    """A TRUST_TRANSITION proves trust_promoter decided. If the harvester
    assigned email_trust itself, no transition would exist and the shared-inbox
    cap would have been bypassed silently."""
    lead = _branch(db, 1, site="https://soleroaster.example")
    _stub(monkeypatch, ["owner@soleroaster.example"], [])

    sh.harvest(db, limit=10, only_missing=True)

    evs = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type == "TRUST_TRANSITION").all()
    assert evs, "no TRUST_TRANSITION — trust was set behind the engine's back"
    db.refresh(lead)
    assert lead.email_source == "WEBSITE"


def test_landline_is_never_seeded_into_whatsapp(db, monkeypatch):
    """0172/0161 numbers reach a desk; WhatsApp cannot reach them at all.
    Assigning one queues a send that can never arrive."""
    lead = _branch(db, 1, site="https://landlineonly.example")
    _stub(monkeypatch, [], ["0172 440 1234"])

    sh.harvest(db, limit=10, only_missing=True)
    db.refresh(lead)

    assert lead.phone == "0172 440 1234", "the number should still be stored to call"
    assert not (lead.whatsapp_number or ""), (
        f"landline {lead.whatsapp_number} was seeded into whatsapp_number")


def test_mobile_is_still_seeded_into_whatsapp(db, monkeypatch):
    """The guard must not block the useful case, or it just breaks enrichment."""
    lead = _branch(db, 1, site="https://mobileonly.example")
    _stub(monkeypatch, [], ["+91-98765-43210"])

    sh.harvest(db, limit=10, only_missing=True)
    db.refresh(lead)

    assert lead.whatsapp_number == "+91-98765-43210"
    assert lead.phone_source == "WEBSITE"


def test_harvester_does_not_import_the_bypassing_grant():
    """contact_trust.grant assigns email_trust directly. If it reappears here,
    the shared-inbox cap is bypassed again."""
    import inspect
    # Strip comments first: the fix documents grant() by name in a comment
    # explaining why it was removed, and matching that would fail the test for
    # the opposite of the reason it exists.
    src = inspect.getsource(sh.harvest)
    code = " ".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "grant(" not in code, "harvest() is calling grant() again"
    assert "tp.evaluate(" in code, "harvest() no longer routes trust through the engine"
