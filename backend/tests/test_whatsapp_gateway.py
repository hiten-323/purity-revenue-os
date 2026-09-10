"""
Automated tests for the WhatsApp Gateway (Phase 2).

All AiSensy calls are mocked. No network. No real messages.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.whatsapp_gateway.client import (
    AiSensyClient,
    extract_message_id,
    normalise_msisdn,
)
from app.services.whatsapp_gateway.consent import check_whatsapp_marketing_consent
from app.services.whatsapp_gateway.idempotency import IdempotencyLedger
from app.services.whatsapp_gateway.models import WhatsAppSendLedger, WhatsAppSendRequest
from app.services.whatsapp_gateway.router import CampaignRouter, resolve_campaign
from app.services.whatsapp_gateway.status_processor import StatusNormalizer


class ConsentTests(unittest.TestCase):
    def test_valid_consent_allowed(self):
        d = check_whatsapp_marketing_consent("SUBSCRIBED")
        self.assertTrue(d.allowed)

    def test_missing_consent_blocked(self):
        d = check_whatsapp_marketing_consent(None)
        self.assertFalse(d.allowed)
        self.assertIn("phone alone", d.reason.lower())

    def test_empty_consent_blocked(self):
        d = check_whatsapp_marketing_consent("")
        self.assertFalse(d.allowed)

    def test_revoked_consent_blocked(self):
        for val in ("UNSUBSCRIBED", "NEVER_SUBSCRIBED", "REVOKED", "OPTED_OUT"):
            d = check_whatsapp_marketing_consent(val)
            self.assertFalse(d.allowed, msg=val)

    def test_unknown_vocabulary_fails_closed(self):
        d = check_whatsapp_marketing_consent("MAYBE")
        self.assertFalse(d.allowed)


class ClientTests(unittest.TestCase):
    def test_normalise_indian_10_digit(self):
        self.assertEqual(normalise_msisdn("9084958495"), "919084958495")

    def test_normalise_already_prefixed(self):
        self.assertEqual(normalise_msisdn("919084958495"), "919084958495")

    def test_normalise_strips_symbols(self):
        self.assertEqual(normalise_msisdn("+91-90849-58495"), "919084958495")

    def test_extract_message_id_prefers_header(self):
        mid = extract_message_id('{"messageId":"body"}', {"x-message-id": "header-1"})
        self.assertEqual(mid, "header-1")

    def test_extract_message_id_from_json(self):
        mid = extract_message_id('{"data":{"messageId":"wamid.ABC"}}', {})
        self.assertEqual(mid, "wamid.ABC")

    def test_extract_message_id_from_messages_array(self):
        mid = extract_message_id('{"messages":[{"id":"wamid.XYZ"}]}', {})
        self.assertEqual(mid, "wamid.XYZ")

    def test_not_configured_without_key(self):
        old = os.environ.pop("EVOLUTION_API_KEY", None)
        try:
            result = AiSensyClient().send(
                campaign_name="PB_TEST",
                destination="919084958495",
            )
            self.assertEqual(result.status, "NOT_CONFIGURED")
            self.assertFalse(result.provider_accepted)
        finally:
            if old is not None:
                os.environ["EVOLUTION_API_KEY"] = old

    def test_invalid_phone_rejected(self):
        os.environ["EVOLUTION_API_KEY"] = "test-key-not-real"
        try:
            result = AiSensyClient().send(
                campaign_name="PB_TEST",
                destination="12",
            )
            self.assertEqual(result.status, "INVALID_REQUEST")
        finally:
            os.environ.pop("EVOLUTION_API_KEY", None)

    def test_successful_provider_response(self):
        class _Resp:
            status_code = 200
            headers = {"x-message-id": "wamid.TEST123"}
            text = '{"messageId":"wamid.TEST123"}'

        class _Client:
            def post(self, *a, **k):
                return _Resp()

        os.environ["EVOLUTION_API_KEY"] = "test-key-not-real"
        try:
            result = AiSensyClient(http_client=_Client()).send(
                campaign_name="PB_TEST",
                destination="919084958495",
                user_name="Test",
            )
            self.assertEqual(result.status, "PROVIDER_ACCEPTED")
            self.assertTrue(result.provider_accepted)
            self.assertFalse(result.delivery_confirmed)
            self.assertEqual(result.message_id, "wamid.TEST123")
        finally:
            os.environ.pop("EVOLUTION_API_KEY", None)

    def test_http_error(self):
        class _Resp:
            status_code = 500
            headers = {}
            text = "internal error"

        class _Client:
            def post(self, *a, **k):
                return _Resp()

        os.environ["EVOLUTION_API_KEY"] = "test-key-not-real"
        try:
            result = AiSensyClient(http_client=_Client()).send(
                campaign_name="PB_TEST",
                destination="919084958495",
            )
            self.assertEqual(result.status, "FAILED")
            self.assertIn("500", result.reason)
        finally:
            os.environ.pop("EVOLUTION_API_KEY", None)

    def test_timeout(self):
        class _Client:
            def post(self, *a, **k):
                raise TimeoutError("simulated")

        os.environ["EVOLUTION_API_KEY"] = "test-key-not-real"
        try:
            result = AiSensyClient(http_client=_Client()).send(
                campaign_name="PB_TEST",
                destination="919084958495",
            )
            self.assertEqual(result.status, "FAILED")
            self.assertIn("TimeoutError", result.reason)
        finally:
            os.environ.pop("EVOLUTION_API_KEY", None)

    def test_secret_not_in_result_reason(self):
        secret = "super-secret-key-abc123"
        os.environ["EVOLUTION_API_KEY"] = secret

        class _Client:
            def post(self, *a, **k):
                raise RuntimeError("boom")

        try:
            result = AiSensyClient(http_client=_Client()).send(
                campaign_name="PB_TEST",
                destination="919084958495",
            )
            self.assertNotIn(secret, result.reason)
            self.assertNotIn(secret, result.raw_response_snippet)
        finally:
            os.environ.pop("EVOLUTION_API_KEY", None)


class RouterTests(unittest.TestCase):
    def test_known_stage(self):
        self.assertEqual(resolve_campaign("ATC"), "PB_ATC_01")
        self.assertEqual(resolve_campaign("checkout"), "PB_CHECKOUT_01")

    def test_explicit_campaign_wins(self):
        self.assertEqual(
            resolve_campaign("ATC", explicit_campaign="CUSTOM_CAMPAIGN"),
            "CUSTOM_CAMPAIGN",
        )

    def test_unknown_stage(self):
        self.assertIsNone(resolve_campaign("UNKNOWN_STAGE_XYZ"))


class StatusNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.n = StatusNormalizer()

    def test_meta_delivered(self):
        payload = {
            "statuses": [
                {"id": "wamid.1", "status": "delivered", "timestamp": "123", "recipient_id": "9198"}
            ]
        }
        r = self.n.normalise(payload)
        self.assertEqual(r.status, "DELIVERED")
        self.assertEqual(r.message_id, "wamid.1")

    def test_meta_read(self):
        payload = {"statuses": [{"id": "wamid.2", "status": "read"}]}
        r = self.n.normalise(payload)
        self.assertEqual(r.status, "READ")

    def test_meta_failed(self):
        payload = {"statuses": [{"id": "wamid.3", "status": "failed"}]}
        r = self.n.normalise(payload)
        self.assertEqual(r.status, "FAILED")

    def test_flat_status(self):
        r = self.n.normalise({"status": "delivered", "messageId": "wamid.4"})
        self.assertEqual(r.status, "DELIVERED")
        self.assertEqual(r.message_id, "wamid.4")

    def test_malformed_string(self):
        r = self.n.normalise("not-json{{{{")
        self.assertEqual(r.status, "UNKNOWN")

    def test_unknown_shape(self):
        r = self.n.normalise({"foo": "bar"})
        self.assertEqual(r.status, "UNKNOWN")

    def test_sent_is_not_delivered(self):
        r = self.n.normalise({"status": "sent", "messageId": "wamid.5"})
        self.assertEqual(r.status, "UNKNOWN")


class RequestModelTests(unittest.TestCase):
    def test_idempotency_key(self):
        r = WhatsAppSendRequest(
            profile_id="p1",
            phone="9198",
            lifecycle_stage="ATC",
            source_event_id="evt-9",
        )
        self.assertEqual(r.idempotency_key, "p1|ATC|evt-9")

    def test_validation(self):
        r = WhatsAppSendRequest(
            profile_id="",
            phone=None,
            lifecycle_stage="",
            source_event_id="",
        )
        errs = r.validate_required()
        self.assertTrue(len(errs) >= 3)


class IdempotencyLedgerUnitTests(unittest.TestCase):
    """Unit-level tests using an in-memory SQLite session."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database.database import Base

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()
        self.ledger = IdempotencyLedger(self.db)

    def tearDown(self):
        self.db.close()

    def test_first_claim_is_new(self):
        row, is_new = self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-1",
            campaign_name="PB_ATC_01",
        )
        self.assertTrue(is_new)
        self.assertEqual(row.status, "PENDING")

    def test_duplicate_event_not_new(self):
        self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-1",
        )
        row2, is_new = self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-1",
        )
        self.assertFalse(is_new)
        self.assertEqual(row2.idempotency_key, "p1|ATC|evt-1")

    def test_already_sent_after_provider_accept(self):
        row, _ = self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-2",
        )
        self.ledger.mark_provider_accepted(row, message_id="wamid.ABC")
        self.assertTrue(IdempotencyLedger.already_sent(row))

        row2, is_new = self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-2",
        )
        self.assertFalse(is_new)
        self.assertTrue(IdempotencyLedger.already_sent(row2))

    def test_status_updates_by_message_id(self):
        row, _ = self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-3",
        )
        self.ledger.mark_provider_accepted(row, message_id="wamid.DEL1")
        updated = self.ledger.apply_status(message_id="wamid.DEL1", status="DELIVERED")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "DELIVERED")
        self.assertIsNotNone(updated.delivered_at)

    def test_read_status(self):
        row, _ = self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-4",
        )
        self.ledger.mark_provider_accepted(row, message_id="wamid.READ1")
        updated = self.ledger.apply_status(message_id="wamid.READ1", status="READ")
        self.assertEqual(updated.status, "READ")
        self.assertIsNotNone(updated.read_at)

    def test_failed_status(self):
        row, _ = self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-5",
        )
        self.ledger.mark_provider_accepted(row, message_id="wamid.FAIL1")
        updated = self.ledger.apply_status(message_id="wamid.FAIL1", status="FAILED")
        self.assertEqual(updated.status, "FAILED")

    def test_idempotency_survives_requery(self):
        """Simulates process restart: new session, same key still found."""
        self.ledger.begin_send(
            profile_id="p1",
            phone="919084958495",
            lifecycle_stage="ATC",
            source_event_id="evt-persist",
        )
        from sqlalchemy.orm import sessionmaker

        Session2 = sessionmaker(bind=self.engine)
        db2 = Session2()
        try:
            ledger2 = IdempotencyLedger(db2)
            row, is_new = ledger2.begin_send(
                profile_id="p1",
                phone="919084958495",
                lifecycle_stage="ATC",
                source_event_id="evt-persist",
            )
            self.assertFalse(is_new)
        finally:
            db2.close()


if __name__ == "__main__":
    unittest.main()
