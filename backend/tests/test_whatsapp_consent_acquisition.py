"""Tests for explicit WhatsApp consent acquisition through email and calls."""
from __future__ import annotations

from app.models.models import B2BLead, Base, WorkflowEvent
from app.services import phone_intelligence as phone
from app.services import whatsapp_consent
from conftest import memory_engine
from sqlalchemy.orm import sessionmaker


def _db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _lead(db, **kwargs):
    lead = B2BLead(
        company=kwargs.pop("company", "Test Distributor"),
        phone=kwargs.pop("phone", "9876500001"),
        email=kwargs.pop("email", "buyer@example.com"),
        **kwargs,
    )
    db.add(lead)
    db.commit()
    return lead


def test_email_request_then_shared_number_grants_whatsapp_consent():
    engine, db = _db()
    try:
        lead = _lead(db)
        whatsapp_consent.record_email_whatsapp_request(
            lead, db, message_id="<request@example>"
        )
        db.commit()

        result = whatsapp_consent.capture_email_reply(
            lead, db, "Yes, please use WhatsApp on +91 9876500002",
            message_id="<reply@example>",
        )
        db.commit()

        assert result["captured"] is True
        assert lead.whatsapp_number == "9876500002"
        assert lead.consent_status == "EXPLICIT"
        assert lead.consent_source == "EMAIL_WHATSAPP_REQUEST"
        assert lead.consent_phone == "9876500002"
        assert lead.consent_timestamp is not None

        event = (db.query(WorkflowEvent)
                 .filter(WorkflowEvent.lead_id == lead.id,
                         WorkflowEvent.event_type == "CONSENT_GIVEN")
                 .one())
        assert event.channel == "email"
        assert event.payload["consent_phone"] == "9876500002"
    finally:
        db.close()
        engine.dispose()


def test_email_number_alone_does_not_grant_without_a_prior_request():
    engine, db = _db()
    try:
        lead = _lead(db)
        result = whatsapp_consent.capture_email_reply(
            lead, db, "9876500002", message_id="<unrelated@example>"
        )
        assert result["captured"] is False
        assert lead.consent_status in (None, "UNKNOWN")
        assert lead.consent_source is None
        assert lead.whatsapp_number is None
    finally:
        db.close()
        engine.dispose()


def test_email_reply_with_multiple_numbers_is_ambiguous_and_blocked():
    engine, db = _db()
    try:
        lead = _lead(db)
        whatsapp_consent.record_email_whatsapp_request(lead, db)
        db.commit()

        result = whatsapp_consent.capture_email_reply(
            lead, db, "Use 9876500002 or 9876500003", message_id="<reply@example>"
        )
        assert result["captured"] is False
        assert "multiple" in result["reason"]
        assert (lead.consent_status or "UNKNOWN") == "UNKNOWN"
    finally:
        db.close()
        engine.dispose()


def test_call_opt_in_can_capture_a_different_whatsapp_number():
    engine, db = _db()
    try:
        lead = _lead(db, phone="9876500001")
        result = phone.log_call(
            lead,
            db,
            "WHATSAPP_CONSENT",
            notes="Buyer explicitly said yes, send the catalogue on WhatsApp to +91 9876500002",
        )
        assert lead.whatsapp_number == "9876500002"
        assert lead.consent_status == "EXPLICIT"
        assert lead.consent_source == "FOUNDER_CALL"
        assert lead.consent_phone == "9876500002"
        assert result["now_emailable"] is not None
    finally:
        db.close()
        engine.dispose()


def test_call_consent_with_multiple_numbers_is_rejected():
    engine, db = _db()
    try:
        lead = _lead(db)
        try:
            phone.log_call(
                lead,
                db,
                "WHATSAPP_CONSENT",
                notes="Send it on WhatsApp to 9876500002 or 9876500003",
            )
        except ValueError as exc:
            assert "multiple phone numbers" in str(exc)
        else:
            raise AssertionError("ambiguous WhatsApp destination was accepted")
        assert (lead.consent_status or "UNKNOWN") == "UNKNOWN"
    finally:
        db.rollback()
        db.close()
        engine.dispose()
