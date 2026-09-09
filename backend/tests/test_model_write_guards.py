"""
Two rules enforced at the attribute, because enforcing them at the callers failed.

This codebase keeps producing the same defect: a rule lives in the call sites,
someone adds a ninth call site, and the rule quietly stops applying there. It
has happened with trust (contact_trust.grant bypassing trust_promoter, twice),
with phone provenance, and with consent.

So these two rules are enforced on the SQLAlchemy attribute itself. The number
of places that assign the field stops mattering.

    lead.email          changing the address zeroes email_confidence
    lead.whatsapp_number  a landline is refused outright

Both guards are written to fail open and loud rather than raise: a guard that
breaks an unrelated write is worse than the defect it prevents.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base
from conftest import memory_engine


@pytest.fixture
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


# ------------------------------------- confidence belongs to an address --

def test_changing_the_address_withdraws_confidence(db):
    """Sending is gated on trust AND confidence >= 40. An inherited score buys
    half the gate for a mailbox that earned nothing."""
    lead = B2BLead(company="Cafe A", email="old@cafe.com")
    lead.email_confidence = 90
    lead.email_verified = True
    db.add(lead)
    db.commit()

    lead.email = "new@cafe.com"

    assert lead.email_confidence == 0
    assert lead.email_verified is False
    assert lead.email_verified_at is None


def test_filling_a_blank_address_also_withdraws_it(db):
    """The live case: 12 leads hold confidence >= 40 with no address at all,
    and website_harvester(only_missing=True) targets exactly those rows."""
    lead = B2BLead(company="Cafe B")
    lead.email_confidence = 65
    db.add(lead)
    db.commit()

    lead.email = "harvested@cafe.com"
    assert lead.email_confidence == 0


def test_rewriting_the_same_address_is_not_a_change(db):
    """Re-saving an unchanged address must not destroy a score it earned."""
    lead = B2BLead(company="Cafe C", email="same@cafe.com")
    db.add(lead)
    db.commit()
    lead.email_confidence = 70
    db.commit()

    lead.email = "  SAME@Cafe.com "        # same mailbox, differently typed
    assert lead.email_confidence == 70


def test_trust_is_left_to_trust_promoter(db):
    """The guard withdraws only the half of the gate that was not earned.
    Demoting trust is evaluate()'s decision, with the full evidence."""
    lead = B2BLead(company="Cafe D", email="old@cafe.com")
    lead.email_trust = "VERIFIED"
    lead.email_confidence = 80
    db.add(lead)
    db.commit()

    lead.email = "new@cafe.com"
    assert lead.email_trust == "VERIFIED"
    assert lead.email_confidence == 0


# ------------------------------------------- whatsapp must be reachable --

@pytest.mark.parametrize("landline", ["01725012345", "0161-2345678", "022-24567890"])
def test_a_landline_is_refused_as_a_whatsapp_number(db, landline):
    lead = B2BLead(company="Kirana A")
    db.add(lead)
    db.commit()

    lead.whatsapp_number = landline
    assert lead.whatsapp_number is None


@pytest.mark.parametrize("mobile", ["9876543210", "+91 98765 43210", "098765 43210"])
def test_a_mobile_still_passes(db, mobile):
    lead = B2BLead(company="Kirana B")
    db.add(lead)
    db.commit()

    lead.whatsapp_number = mobile
    assert lead.whatsapp_number == mobile


def test_the_guard_survives_the_constructor(db):
    """A landline passed at construction time must not slip past."""
    lead = B2BLead(company="Kirana C", whatsapp_number="0172-5012345")
    db.add(lead)
    db.commit()
    assert lead.whatsapp_number is None


def test_mirroring_phone_into_whatsapp_cannot_queue_a_dead_send(db):
    """The manual-edit endpoint mirrored phone -> whatsapp_number straight
    across. That is where most of the 212 undeliverable rows came from."""
    lead = B2BLead(company="Kirana D")
    db.add(lead)
    db.commit()

    lead.phone = "01725012345"
    lead.whatsapp_number = lead.phone

    assert lead.phone == "01725012345", "the number itself must be kept — it is callable"
    assert lead.whatsapp_number is None, "but not messageable"


def test_retval_is_actually_wired(db):
    """A set-listener's return value is discarded unless retval=True. The first
    version of the landline guard ran on every write and changed nothing, so
    this asserts the wiring rather than the intent."""
    lead = B2BLead(company="Kirana E")
    db.add(lead)
    db.commit()
    lead.whatsapp_number = "0172-5012345"
    assert lead.whatsapp_number is None, (
        "guard returned a value that SQLAlchemy discarded — retval=True missing")
