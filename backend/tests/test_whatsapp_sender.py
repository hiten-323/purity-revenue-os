"""Regression tests for the AiSensy transport boundary.

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

    def test_success_means_provider_accepted_not_delivered(self):
        old_config = os.environ.get("AISENSY_API_KEY")
        old_campaign = os.environ.get("AISENSY_CAMPAIGN_NAME")
        old_client = ws.httpx.Client
        try:
            os.environ["AISENSY_API_KEY"] = "test-key"
            os.environ["AISENSY_CAMPAIGN_NAME"] = "test-campaign"
            ws.httpx.Client = _FakeClient
            result = ws.send_whatsapp(self.lead, "hello")
            self.assertEqual(result.status, "sent")
            self.assertTrue(result.provider_accepted)
            self.assertFalse(result.delivery_confirmed)
            self.assertEqual(result.message_id, "hdr-123")
        finally:
            ws.httpx.Client = old_client
            if old_config is None:
                os.environ.pop("AISENSY_API_KEY", None)
            else:
                os.environ["AISENSY_API_KEY"] = old_config
            if old_campaign is None:
                os.environ.pop("AISENSY_CAMPAIGN_NAME", None)
            else:
                os.environ["AISENSY_CAMPAIGN_NAME"] = old_campaign


if __name__ == "__main__":
    unittest.main()
