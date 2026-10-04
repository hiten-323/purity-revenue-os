"""A successful outreach email must not be sent again.

Production failure: the worker transmitted, then committed the proof on the
same session it had held open across SMTP. A SQLite lock or a crash rolled
that proof back. The next cycle still saw "never emailed" and sent the same
intro again. Pace limits did not save it — the repeat was a later cycle.

These tests refuse the second attempt after a recorded success, including
when the caller's session rolls back, and they check that a failed attempt
is not stored as a success.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import smtplib
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, WorkflowEvent
from app.services.email_send_ledger import (
    CLAIMED,
    FAILED,
    SENT,
    EmailSendLedger,
)
from conftest import memory_engine


class _SMTP:
    calls = 0
    fail = False
    quit_fails = False

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.quit_fails and exc_type is None:
            raise smtplib.SMTPException("connection closed after accept")
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, *args):
        pass

    def sendmail(self, *args, **kwargs):
        type(self).calls += 1
        if self.fail:
            raise smtplib.SMTPException("451 temporary failure")


@pytest.fixture
def db(monkeypatch):
    import app.database.database as dbmod
    import app.services.email_send_ledger  # noqa: F401
    from app.database.database import Base
    from app.services import email_sender

    engine = memory_engine()
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(dbmod, "SessionLocal", Session)
    monkeypatch.setattr(email_sender.smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(email_sender, "SENDER_PASSWORD", "test-only")
    monkeypatch.setattr(email_sender, "domain_is_deliverable", lambda _addr: True)
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "0")
    _SMTP.calls = 0
    _SMTP.fail = False
    _SMTP.quit_fails = False
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _lead(db, **kw):
    defaults = dict(
        company="Alt Space Roasters",
        email="buyer@altspace.example",
        contact_name="Buyer",
        division="corporate",
        industry="IT office",
        city="Mohali",
        status="DISCOVERED",
        email_trust="VERIFIED",
        email_confidence=80,
        email_source="FOUNDER_CALL",
        email_verification_status="FOUNDER_CALL_PROVIDED",
        coffee_buying_score=70,
        contact_status="CONTACTABLE",
    )
    defaults.update(kw)
    lead = B2BLead(**defaults)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def _email(lead, *, stage="intro", body="Hi there, Purity Beans intro for your office."):
    from app.services.email_sender import OutreachEmail
    return OutreachEmail(
        to_email=lead.email,
        to_name=lead.contact_name or "",
        company=lead.company or "",
        subject=f"Purity Beans — {stage}",
        body_text=body,
        lead_id=lead.id,
        stage=stage,
    )


def _send(email):
    from app.services.email_sender import send_email
    return send_email(email)


def _backdate_proof(db, hours=3):
    """Move the recorded send outside the pace window so the next attempt is not held for timing."""
    when = datetime.utcnow() - timedelta(hours=hours)
    for event in db.query(WorkflowEvent).filter(WorkflowEvent.event_type == "EMAIL_SENT").all():
        event.occurred_at = when
    for row in db.query(EmailSendLedger).all():
        if row.sent_at:
            row.sent_at = when
        if row.claimed_at:
            row.claimed_at = when
    db.commit()


def test_ledger_read_keeps_an_email_captured_on_the_same_session(db):
    """Cadence may consult the ledger while a call is still uncommitted.

    Reading the ledger used to roll that transaction back, so EMAIL_COLLECTED
    stored nothing and the address was not sendable.
    """
    from app.services import sequence_engine as se
    from app.services.email_send_ledger import sent_markers, table_ready

    lead = _lead(db, email="")
    lead.email = "purchase@bigtraders.in"
    lead.email_trust = "VERIFIED"
    lead.email_confidence = 80
    db.flush()

    assert table_ready(db) is True
    assert sent_markers(db, lead) == []
    assert se.state(lead, db)["reason"] == "not_started"

    db.commit()
    db.refresh(lead)
    assert lead.email == "purchase@bigtraders.in"


def test_second_send_is_refused_after_success_even_if_caller_rolls_back(db):
    lead = _lead(db)
    first = _send(_email(lead))
    assert first.status == "sent", first.error
    assert _SMTP.calls == 1

    db.expire_all()
    assert lead.status == "EMAIL_SENT"
    assert lead.email_sequence_last_sent is not None
    assert (lead.email_sequence_stage or 0) >= 1
    assert db.query(WorkflowEvent).filter(WorkflowEvent.event_type == "EMAIL_SENT").count() == 1
    assert db.query(EmailSendLedger).filter(EmailSendLedger.status == SENT).count() == 1

    # The worker session used to roll back AFTER SMTP and take the proof with it.
    caller = sessionmaker(bind=db.get_bind())()
    caller.add(WorkflowEvent(
        lead_id=lead.id, event_type="EMAIL_FAILED", actor="TEST", channel="email",
        payload={"note": "pending row the caller will abandon"},
        occurred_at=datetime.utcnow(),
    ))
    caller.rollback()
    caller.close()

    db.expire_all()
    assert db.query(EmailSendLedger).filter(EmailSendLedger.status == SENT).count() == 1
    _backdate_proof(db)

    second = _send(_email(lead, body="Hi there, a slightly rewritten intro."))
    assert second.status == "failed"
    assert "already sent" in (second.error or "")
    assert "same template/stage" in (second.error or "")
    assert _SMTP.calls == 1


def test_identical_body_is_refused_even_with_a_different_stage_label(db):
    lead = _lead(db)
    body = "Hi there, the exact same outreach letter."
    first = _send(_email(lead, stage="intro", body=body))
    assert first.status == "sent", first.error
    _backdate_proof(db)

    second = _send(_email(lead, stage="nudge", body=body))
    assert second.status == "failed"
    assert "identical body" in (second.error or "")
    assert _SMTP.calls == 1


def test_a_later_touch_with_a_different_body_is_still_allowed(db):
    lead = _lead(db)
    first = _send(_email(lead, stage="intro", body="Intro letter, day zero."))
    assert first.status == "sent", first.error
    _backdate_proof(db)

    follow = _send(_email(lead, stage="nudge", body="Short follow-up, different letter."))
    assert follow.status == "sent", follow.error
    assert _SMTP.calls == 2


def test_same_stage_to_a_different_mailbox_is_allowed(db):
    first_lead = _lead(db, company="Alt Space Roasters", email="buyer@altspace.example")
    other = _lead(db, company="North Cafe", email="owner@northcafe.example")
    assert _send(_email(first_lead)).status == "sent"
    _backdate_proof(db)
    assert _send(_email(other, body="A different company's intro.")).status == "sent"
    assert _SMTP.calls == 2


def test_same_mailbox_on_another_lead_row_is_still_one_send(db):
    first_lead = _lead(db, company="Alt Space Roasters", email="buyer@altspace.example")
    duplicate_row = _lead(db, company="Alt Space Branch", email="buyer@altspace.example")
    assert _send(_email(first_lead)).status == "sent"
    _backdate_proof(db)
    second = _send(_email(duplicate_row, body="Intro addressed to the same inbox."))
    assert second.status == "failed"
    assert "already sent" in (second.error or "")
    assert _SMTP.calls == 1


def test_failed_send_is_not_success_and_can_be_retried_once(db):
    lead = _lead(db)
    _SMTP.fail = True
    failed = _send(_email(lead))
    assert failed.status == "failed"
    assert "451" in (failed.error or "")
    db.expire_all()
    assert lead.status != "EMAIL_SENT"
    assert db.query(WorkflowEvent).filter(WorkflowEvent.event_type == "EMAIL_SENT").count() == 0
    row = db.query(EmailSendLedger).one()
    assert row.status == FAILED
    assert row.sent_at is None

    _SMTP.fail = False
    retried = _send(_email(lead))
    assert retried.status == "sent", retried.error
    db.expire_all()
    assert lead.status == "EMAIL_SENT"
    assert db.query(EmailSendLedger).filter(EmailSendLedger.status == SENT).count() == 1
    assert _SMTP.calls == 2

    _backdate_proof(db)
    third = _send(_email(lead, body="Another intro after the one that landed."))
    assert third.status == "failed"
    assert _SMTP.calls == 2


def test_smtp_accept_then_quit_error_is_still_a_recorded_send(db):
    lead = _lead(db)
    _SMTP.quit_fails = True
    result = _send(_email(lead))
    assert result.status == "sent", result.error
    db.expire_all()
    assert db.query(EmailSendLedger).filter(EmailSendLedger.status == SENT).count() == 1
    _SMTP.quit_fails = False
    _backdate_proof(db)
    again = _send(_email(lead, body="Retry after a scary disconnect."))
    assert again.status == "failed"
    assert _SMTP.calls == 1


def test_unresolved_claim_blocks_a_resend(db):
    lead = _lead(db)
    db.add(EmailSendLedger(
        idempotency_key="crash-before-outcome",
        lead_id=lead.id,
        to_email=lead.email.lower(),
        stage="intro",
        body_hash="not-the-body",
        status=CLAIMED,
        claimed_at=datetime.utcnow(),
    ))
    db.commit()

    result = _send(_email(lead, body="Intro the worker is about to retry."))
    assert result.status == "failed"
    assert "in flight" in (result.error or "")
    assert _SMTP.calls == 0
    db.expire_all()
    assert lead.status != "EMAIL_SENT"
    assert db.query(WorkflowEvent).filter(WorkflowEvent.event_type == "EMAIL_SENT").count() == 0


def test_ledger_send_without_workflow_event_is_not_immediately_due(db):
    """Caller rollback used to delete the only EMAIL_SENT row. The ledger still counts."""
    from app.services.sequence_engine import state

    lead = _lead(db)
    db.add(EmailSendLedger(
        idempotency_key="ledger-only",
        lead_id=lead.id,
        to_email=lead.email.lower(),
        stage="intro",
        body_hash="abc",
        status=SENT,
        message_id="<ledger-only@test>",
        claimed_at=datetime.utcnow(),
        sent_at=datetime.utcnow(),
    ))
    db.commit()

    st = state(lead, db)
    assert st["touches"] >= 1
    assert st["next_touch"] == "nudge"
    assert st["ready"] is False


def test_failed_ledger_row_does_not_advance_cadence_or_block_the_intro(db):
    from app.services.sequence_engine import state

    lead = _lead(db)
    db.add(EmailSendLedger(
        idempotency_key="failed-only",
        lead_id=lead.id,
        to_email=lead.email.lower(),
        stage="intro",
        body_hash="abc",
        status=FAILED,
        error="451 temporary failure",
        claimed_at=datetime.utcnow(),
    ))
    db.commit()

    st = state(lead, db)
    assert st["touches"] == 0
    assert st["next_touch"] == "intro"
    assert st["ready"] is True


def test_claimed_intro_is_not_ready_to_send_again(db):
    from app.services.sequence_engine import state

    lead = _lead(db)
    db.add(EmailSendLedger(
        idempotency_key="claimed-intro",
        lead_id=lead.id,
        to_email=lead.email.lower(),
        stage="intro",
        body_hash="abc",
        status=CLAIMED,
        claimed_at=datetime.utcnow(),
    ))
    db.commit()

    st = state(lead, db)
    assert st["ready"] is False
    assert st["next_touch"] == "intro"


def test_execute_one_skips_a_repeat_when_cadence_cannot_see_the_send(db, monkeypatch):
    """The worker path that ignored cooldown after a lost EMAIL_SENT row."""
    from app.services.smart_outreach import execute_one

    lead = _lead(db)
    db.add(EmailSendLedger(
        idempotency_key="already-intro",
        lead_id=lead.id,
        to_email=lead.email.lower(),
        stage="intro",
        body_hash="abc",
        status=SENT,
        message_id="<intro@test>",
        claimed_at=datetime.utcnow() - timedelta(days=1),
        sent_at=datetime.utcnow() - timedelta(days=1),
    ))
    db.commit()

    def _must_not_send(email):
        raise AssertionError("execute_one tried to send a recorded intro")

    monkeypatch.setattr(
        "app.services.smart_outreach.plan_touch",
        lambda *args, **kwargs: {
            "action": "WARM_FIRST_TOUCH",
            "channel": "email",
            "execute": True,
            "reason": "cadence blind — still thinks the intro is due",
        },
    )
    monkeypatch.setattr(
        "app.services.smart_outreach._sequence_state",
        lambda *args, **kwargs: {"next_touch": "intro", "touches": 0},
    )
    monkeypatch.setattr("app.services.email_sender.send_email", _must_not_send)

    result = execute_one(db, lead)
    assert result["status"] == "SKIPPED"
    assert "already sent" in (result.get("reason") or "")


def test_plan_touch_does_not_execute_a_just_sent_intro(db):
    from app.services.smart_outreach import plan_touch

    lead = _lead(db)
    db.add(EmailSendLedger(
        idempotency_key="just-sent",
        lead_id=lead.id,
        to_email=lead.email.lower(),
        stage="intro",
        body_hash="abc",
        status=SENT,
        message_id="<just-sent@test>",
        claimed_at=datetime.utcnow(),
        sent_at=datetime.utcnow(),
    ))
    db.commit()

    decision = plan_touch(db, lead)
    assert decision["execute"] is False
