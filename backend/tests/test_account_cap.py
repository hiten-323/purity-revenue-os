"""
Account-level frequency cap: every channel consumes the same slot.

The cap exists because 26 More Supermarket branches share one inbox, and
emailing all of them is one relationship contacted 26 times in a day. It used
to count EMAIL_SENT only, so a WhatsApp touch cost an account nothing and the
same 26 branches could be messaged through the other door without the guard
firing once.

Eleven checks — identical to the previous standalone script, expressed as
pytest asserts (no sys.exit).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, B2BLead, WorkflowEvent
from app.services import account_graph as ag

# Ensure fail-closed EMAIL_SENT → EMAIL_SENT_UNPROVEN listener is registered
# when this module is imported under pytest (same contract as production).
try:
    from app.models import send_proof_listener as _spl

    _spl.install()
except Exception:
    pass


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    session = Session()
    yield session
    session.close()
    eng.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def _branch(db, n: int) -> B2BLead:
    lead = B2BLead(
        company=f"More Supermarket {n}",
        city="Abohar",
        website="more.in",
        email=f"b{n}@more.in",
        division="RETAIL",
    )
    db.add(lead)
    db.commit()
    return lead


def _touch(db, lead: B2BLead, kind: str, days_ago: int = 0, mid: str | None = None) -> None:
    """A PROVEN send. EMAIL_SENT without both payload["to"] and
    payload["message_id"] is renamed EMAIL_SENT_UNPROVEN by a before_insert
    listener, so a test that omits them silently asserts nothing — the key is
    "to", not "recipient"."""
    db.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type=kind,
            actor="SYSTEM",
            channel="email" if kind == "EMAIL_SENT" else "whatsapp",
            payload={
                "to": lead.email,
                "message_id": mid or f"<{kind}-{lead.id}-{days_ago}@test>",
            },
            occurred_at=datetime.utcnow() - timedelta(days=days_ago),
        )
    )
    db.commit()


def _clear(db) -> None:
    db.query(WorkflowEvent).delete()
    db.commit()


def test_account_cap_eleven_checks(db):
    """All 11 account-cap checks (same conditions as the old script)."""
    a, b, c, d = _branch(db, 1), _branch(db, 2), _branch(db, 3), _branch(db, 4)

    # 1 — account resolution
    acct = ag.account_for(a, db)
    assert acct["branch_count"] == 4, f"branches resolved: {acct['branch_count']}"

    # 2–3 — WhatsApp consumes the slot
    _clear(db)
    _touch(db, a, "WHATSAPP_SENT")
    ok, why = ag.can_contact_new(b, db)
    assert ok is False, "WhatsApp did not consume the account cooldown"
    assert "WhatsApp" in why, f"channel not named in the reason: {why}"

    # 4–5 — email behaves identically
    _clear(db)
    _touch(db, a, "EMAIL_SENT")
    ok, why = ag.can_contact_new(c, db)
    assert ok is False, "email did not consume the account cooldown"
    assert "email" in why, f"channel not named in the reason: {why}"

    # 6–7 — chain rule applies to WhatsApp
    # 4 branches is a chain, so past the 7-day cooldown the 21-day corporate-first
    # rule still holds. Asserting expiry at 8 days would be asserting the wrong
    # rule and would "fail" against correct behaviour.
    _clear(db)
    _touch(db, a, "WHATSAPP_SENT", days_ago=ag.COOLDOWN_DAYS + 1)
    ok, why = ag.can_contact_new(b, db)
    assert ok is False, "chain rule did not apply to a WhatsApp touch"
    assert "chain" in why, f"reason does not cite the chain rule: {why}"

    # 8 — both rules eventually expire
    _clear(db)
    _touch(db, a, "WHATSAPP_SENT", days_ago=ag.CHAIN_CORPORATE_FIRST_DAYS + 1)
    ok, _ = ag.can_contact_new(b, db)
    assert ok is True, "WhatsApp cooldown never expires"

    # 9–10 — unproven sends must NOT consume a slot
    _clear(db)
    db.add(
        WorkflowEvent(
            lead_id=a.id,
            event_type="EMAIL_SENT",
            actor="SYSTEM",
            channel="email",
            payload={},  # no proof at all
            occurred_at=datetime.utcnow(),
        )
    )
    db.commit()
    stored = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == a.id).first()
    assert stored.event_type == "EMAIL_SENT_UNPROVEN", f"unproven send stored as {stored.event_type}"
    ok, why = ag.can_contact_new(d, db)
    assert ok is True, "an unproven send consumed a real account slot"

    # 11 — a reply still closes the account to cold outreach
    _clear(db)
    db.add(
        WorkflowEvent(
            lead_id=a.id,
            event_type="WHATSAPP_REPLY",
            actor="BUYER",
            channel="whatsapp",
            payload={},
            occurred_at=datetime.utcnow(),
        )
    )
    db.commit()
    ok, why = ag.can_contact_new(b, db)
    assert ok is False, "a live conversation did not pause cold outreach to siblings"
