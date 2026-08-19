"""Lifecycle intent + decision safety (no network, no live sends)."""
from datetime import datetime

from app.services.outreach_lifecycle import infer_intent
from app.services.smart_outreach import render_email


def _event(event_type, payload=None, channel="email"):
    class E:
        pass

    e = E()
    e.event_type = event_type
    e.payload = payload or {}
    e.channel = channel
    e.lead_id = 1
    e.id = 1
    e.occurred_at = datetime.utcnow()
    return e


def test_intent_catalogue_request():
    assert (
        infer_intent(_event("EMAIL_REPLY_RECEIVED", {"body": "Yes, send the catalogue"}))
        == "CATALOGUE_REQUESTED"
    )


def test_intent_pricing_request():
    assert (
        infer_intent(
            _event("WHATSAPP_REPLY", {"body": "What is your wholesale price and MOQ?"}, "whatsapp")
        )
        == "PRICING_REQUESTED"
    )


def test_intent_opt_out_remove_me():
    assert infer_intent(_event("EMAIL_REPLY_RECEIVED", {"body": "Please remove me"})) == "OPTED_OUT"


def test_intent_not_interested():
    assert (
        infer_intent(_event("EMAIL_REPLY_RECEIVED", {"body": "Not interested, no thanks"}))
        == "NOT_INTERESTED"
    )


def test_horeca_subject_interpolates_company():
    class L:
        contact_name = "Asha"
        company = "Cafe Mocha"
        city = "Pune"

    class P:
        category = "HORECA"

    subject, body = render_email(L(), P(), 1)
    assert "Cafe Mocha" in subject
    assert "{company}" not in subject
