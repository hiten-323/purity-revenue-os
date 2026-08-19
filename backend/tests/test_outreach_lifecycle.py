from datetime import datetime

from app.services.outreach_lifecycle import infer_intent


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
    assert infer_intent(_event("EMAIL_REPLY_RECEIVED", {"body": "Yes, send the catalogue"})) == "CATALOGUE_REQUESTED"


def test_intent_pricing_request():
    assert infer_intent(_event("WHATSAPP_REPLY", {"body": "What is your wholesale price and MOQ?"}, "whatsapp")) == "PRICING_REQUESTED"


def test_intent_opt_out():
    assert infer_intent(_event("EMAIL_REPLY_RECEIVED", {"body": "Please remove me"})) == "OPTED_OUT"
