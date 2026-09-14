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

    def test_success_means_provider_accepted_not_delivered(self):
        """The contract is unchanged; only who holds the socket moved.

        whatsapp_sender no longer makes the HTTP call — whatsapp_aisensy
        does — so the fake client is installed there. Every assertion below is
        the original one: a 2xx means the provider ACCEPTED the request, and
        nothing about delivery.
        """
        from app.services import whatsapp_aisensy as transport

        keys = ("AISENSY_ENABLED", "AISENSY_API_KEY", "WHATSAPP_TEMPLATE")
        old_env = {k: os.environ.get(k) for k in keys}
        old_client = transport.httpx.Client
        try:
            os.environ["AISENSY_ENABLED"] = "1"
            os.environ["AISENSY_API_KEY"] = "test-key"
            os.environ["WHATSAPP_TEMPLATE"] = "test_campaign"
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
