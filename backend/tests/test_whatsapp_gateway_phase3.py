"""
Phase 3 hardening tests: webhook auth fail-closed, concurrent idempotency, sandbox.
No network. No real secrets.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.whatsapp_gateway.security import is_production, require_admin_secret, verify_webhook
from app.services.whatsapp_gateway.sandbox import run_sandbox_send
from app.services.whatsapp_gateway.models import WhatsAppSendRequest
from app.services.whatsapp_gateway.idempotency import IdempotencyLedger


def _hmac(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = {}
        for k in (
            "ENVIRONMENT",
            "ENV",
            "KLAVIYO_WEBHOOK_SECRET",
            "AISENSY_WEBHOOK_SECRET",
            "GATEWAY_ADMIN_SECRET",
            "WEBHOOK_MAX_SKEW_SECONDS",
        ):
            self._env_backup[k] = os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_production_rejects_missing_secret(self):
        os.environ["ENVIRONMENT"] = "production"
        r = verify_webhook(
            provider="klaviyo",
            headers={},
            body=b"{}",
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertFalse(r.allowed)
        self.assertIn("required", r.reason.lower())

    def test_dev_bypass_when_secret_unset(self):
        os.environ.pop("ENVIRONMENT", None)
        r = verify_webhook(
            provider="klaviyo",
            headers={},
            body=b"{}",
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertTrue(r.allowed)
        self.assertEqual(r.mode, "bypass_dev")

    def test_shared_secret_header_accepted(self):
        os.environ["ENVIRONMENT"] = "production"
        os.environ["KLAVIYO_WEBHOOK_SECRET"] = "test-secret-xyz"
        r = verify_webhook(
            provider="klaviyo",
            headers={"X-Webhook-Secret": "test-secret-xyz"},
            body=b'{"a":1}',
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertTrue(r.allowed)
        self.assertEqual(r.mode, "shared_secret")

    def test_wrong_secret_rejected(self):
        os.environ["ENVIRONMENT"] = "production"
        os.environ["KLAVIYO_WEBHOOK_SECRET"] = "correct"
        r = verify_webhook(
            provider="klaviyo",
            headers={"X-Webhook-Secret": "wrong"},
            body=b"{}",
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertFalse(r.allowed)

    def test_hmac_accepted(self):
        os.environ["ENVIRONMENT"] = "production"
        secret = "hmac-secret"
        os.environ["KLAVIYO_WEBHOOK_SECRET"] = secret
        body = b'{"event":"test"}'
        sig = _hmac(secret, body)
        r = verify_webhook(
            provider="klaviyo",
            headers={"X-Webhook-Signature": f"sha256={sig}"},
            body=body,
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertTrue(r.allowed)
        self.assertEqual(r.mode, "hmac")

    def test_hmac_tampered_body_rejected(self):
        os.environ["ENVIRONMENT"] = "production"
        secret = "hmac-secret"
        os.environ["KLAVIYO_WEBHOOK_SECRET"] = secret
        body = b'{"event":"test"}'
        sig = _hmac(secret, body)
        r = verify_webhook(
            provider="klaviyo",
            headers={"X-Webhook-Signature": f"sha256={sig}"},
            body=b'{"event":"TAMPERED"}',
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertFalse(r.allowed)

    def test_timestamp_skew_rejected(self):
        os.environ["ENVIRONMENT"] = "production"
        os.environ["KLAVIYO_WEBHOOK_SECRET"] = "sec"
        os.environ["WEBHOOK_MAX_SKEW_SECONDS"] = "60"
        r = verify_webhook(
            provider="klaviyo",
            headers={
                "X-Webhook-Secret": "sec",
                "X-Webhook-Timestamp": "1000000000",  # far in the past
            },
            body=b"{}",
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertFalse(r.allowed)
        self.assertIn("skew", r.reason.lower())

    def test_admin_secret_required_in_production(self):
        os.environ["ENVIRONMENT"] = "production"
        r = require_admin_secret({})
        self.assertFalse(r.allowed)

    def test_secret_not_in_auth_reason_on_mismatch(self):
        os.environ["ENVIRONMENT"] = "production"
        secret = "very-secret-value-do-not-leak"
        os.environ["KLAVIYO_WEBHOOK_SECRET"] = secret
        r = verify_webhook(
            provider="klaviyo",
            headers={"X-Webhook-Secret": "nope"},
            body=b"{}",
            secret_env_var="KLAVIYO_WEBHOOK_SECRET",
        )
        self.assertFalse(r.allowed)
        self.assertNotIn(secret, r.reason)


class ConcurrentIdempotencyTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database.database import Base

        # file-based sqlite so connections from threads share the same DB
        self.engine = create_engine(
            "sqlite:///file:wa_gateway_concurrent?mode=memory&cache=shared",
            connect_args={"check_same_thread": False, "uri": True},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_concurrent_claims_only_one_new(self):
        results = []

        def claim():
            db = self.Session()
            try:
                ledger = IdempotencyLedger(db)
                row, is_new = ledger.begin_send(
                    profile_id="p-concurrent",
                    phone="919084958495",
                    lifecycle_stage="CHECKOUT",
                    source_event_id="evt-concurrent-1",
                    campaign_name="PB_CHECKOUT_01",
                )
                results.append(is_new)
                return is_new
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(claim) for _ in range(8)]
            for f in as_completed(futures):
                f.result()

        self.assertEqual(sum(1 for x in results if x), 1)
        self.assertEqual(sum(1 for x in results if not x), 7)


class SandboxTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database.database import Base

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        self.db = Session()

    def tearDown(self):
        self.db.close()

    def _req(self, **kwargs):
        defaults = dict(
            profile_id="sandbox-p1",
            phone="919084958495",
            lifecycle_stage="CHECKOUT",
            source_event_id="sandbox-evt-1",
            whatsapp_marketing_consent="SUBSCRIBED",
        )
        defaults.update(kwargs)
        return WhatsAppSendRequest(**defaults)

    def test_subscribed_allowed_sandbox(self):
        r = run_sandbox_send(self.db, self._req())
        self.assertTrue(r.ok)
        self.assertEqual(r.action, "sandbox_sent")
        self.assertTrue(r.fake_message_id.startswith("sandbox.wamid."))

    def test_null_consent_blocked(self):
        r = run_sandbox_send(self.db, self._req(whatsapp_marketing_consent=None))
        self.assertEqual(r.status, "BLOCKED_CONSENT")

    def test_unsubscribed_blocked(self):
        r = run_sandbox_send(self.db, self._req(whatsapp_marketing_consent="UNSUBSCRIBED"))
        self.assertEqual(r.status, "BLOCKED_CONSENT")

    def test_missing_phone_blocked(self):
        r = run_sandbox_send(self.db, self._req(phone=None))
        self.assertEqual(r.status, "BLOCKED_PHONE")

    def test_invalid_phone_blocked(self):
        r = run_sandbox_send(self.db, self._req(phone="12"))
        self.assertEqual(r.status, "BLOCKED_PHONE")

    def test_duplicate_blocked(self):
        r1 = run_sandbox_send(self.db, self._req())
        self.assertEqual(r1.action, "sandbox_sent")
        r2 = run_sandbox_send(self.db, self._req())
        self.assertEqual(r2.action, "duplicate")


if __name__ == "__main__":
    unittest.main()
