"""Regression tests for the WhatsApp transport boundary.

The transport is Evolution API in Meta Cloud API mode. It was AiSensy,
and it was three separate AiSensy clients; the assertions here did not
change when that was consolidated, only the environment variables and
which module owns the socket.

These tests never make a network request and never require live credentials.
They pin the distinction between provider acceptance and recipient delivery.
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import whatsapp_sender as ws


class _FakeResponse:
    status_code = 200
    headers = {"x-message-id": "hdr-123"}
    text = '{"messageId":"body-456"}'


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return _FakeResponse()


class WhatsAppSenderTests(unittest.TestCase):
    def setUp(self):
        self.lead = SimpleNamespace(
            consent_status="EXPLICIT",
            do_not_call=False,
            status="REPLIED",
            whatsapp_number="9084958495",
            phone="",
            contact_name="Test Buyer",
            company="Test Co",
            last_reply_at=None,
            last_updated=None,
        )

    def test_provider_message_id_prefers_header(self):
        got = ws._extract_provider_message_id('{"messageId":"body"}', {"x-message-id": "header"})
        self.assertEqual(got, "header")

    def test_provider_message_id_falls_back_to_json(self):
        got = ws._extract_provider_message_id('{"data":{"messageId":"body-123"}}', {})
        self.assertEqual(got, "body-123")

    def test_cold_unknown_consent_is_blocked(self):
        self.lead.consent_status = "UNKNOWN"
        self.lead.status = "DISCOVERED"
        ok, _ = ws.consent_check(self.lead)
        self.assertFalse(ok)

    def test_explicit_consent_is_allowed(self):
        ok, _ = ws.consent_check(self.lead)
        self.assertTrue(ok)

    def test_implied_b2b_consent_is_not_allowed(self):
        self.lead.consent_status = "IMPLIED_B2B"
        self.lead.status = "DISCOVERED"
        ok, _ = ws.consent_check(self.lead)
        self.assertFalse(ok)

    def test_consent_without_a_bound_number_still_works(self):
        """Backward compatibility: consent recorded before consent_phone
        existed, or via a provenance that never sets it (phone_intelligence's
        FOUNDER_CALL), has no attribute at all on a real ORM row -- but even
        an explicit None must not become a spurious mismatch."""
        self.lead.consent_phone = None
        ok, _ = ws.consent_check(self.lead)
        self.assertTrue(ok)

    def test_consent_is_refused_when_the_number_has_changed_since(self):
        """The adversarial case: WhatsApp opt-in was given on one number, and
        the lead's number was silently changed afterward (re-enrichment, a
        manual fix, or the corruption bug that put one fabricated number on
        1,166 leads). The opt-in must not follow the row to a new
        destination nobody at that number ever agreed to."""
        self.lead.consent_phone = "9876543210"     # what was actually consented
        self.lead.whatsapp_number = "9111122223"   # the number on file now
        ok, reason = ws.consent_check(self.lead)
        self.assertFalse(ok)
        self.assertIn("different number", reason)

    def test_consent_survives_cosmetic_formatting_differences(self):
        """The bound number and the current number are the same subscriber,
        just formatted differently -- must not be treated as a mismatch."""
        self.lead.consent_phone = "+91-90849-58495"
        self.lead.whatsapp_number = "9084958495"
        ok, _ = ws.consent_check(self.lead)
        self.assertTrue(ok)

    def test_do_not_call_overrides_a_pre_existing_consent(self):
        """An opt-out must win even over consent already on record. This is
        the case that matters: a lead consented once (any provenance), then
        later asked to stop -- do_not_call is checked before consent_status,
        so the earlier EXPLICIT never re-opens the door."""
        self.lead.consent_status = "EXPLICIT"
        self.lead.consent_phone = self.lead.whatsapp_number
        self.lead.do_not_call = True
        ok, reason = ws.consent_check(self.lead)
        self.assertFalse(ok)
        self.assertIn("do-not-contact", reason)

    def test_success_means_provider_accepted_not_delivered(self):
        """The contract is unchanged; only who holds the socket moved.

        whatsapp_sender no longer makes the HTTP call — whatsapp_aisensy
        does — so the fake client is installed there. Every assertion below is
        the original one: a 2xx means the provider ACCEPTED the request, and
        nothing about delivery.
        """
        from app.services import whatsapp_aisensy as transport

        keys = ("AISENSY_ENABLED", "AISENSY_API_KEY", "WHATSAPP_TEMPLATE", "WHATSAPP_CAMPAIGN_LIVE")
        old_env = {k: os.environ.get(k) for k in keys}
        old_client = transport.httpx.Client
        try:
            os.environ["AISENSY_ENABLED"] = "1"
            os.environ["AISENSY_API_KEY"] = "test-key"
            os.environ["WHATSAPP_TEMPLATE"] = "test_campaign"
            os.environ["WHATSAPP_CAMPAIGN_LIVE"] = "1"
            transport.httpx.Client = _FakeClient

            result = ws.send_whatsapp(self.lead, "hello")
            self.assertEqual(result.status, "sent")
            self.assertTrue(result.provider_accepted)
            self.assertFalse(result.delivery_confirmed)
            # Header first, then body. AiSensy's documented success contract
            # promises no id at all, so the extractor takes the most
            # authoritative one available rather than a fixed location.
            self.assertEqual(result.message_id, "hdr-123")
        finally:
            transport.httpx.Client = old_client
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
