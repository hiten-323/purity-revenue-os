from __future__ import annotations

import base64
import hashlib
import hmac


def test_shopify_hmac_uses_base64(monkeypatch):
    from app.services.shopify_security import verify_shopify_hmac

    body = b'{"id":123}'
    secret = "test-shopify-secret"
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", secret)
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("ascii")

    assert verify_shopify_hmac(body, signature) is True
    assert verify_shopify_hmac(body, digest.hex()) is False
    assert verify_shopify_hmac(body, "") is False


def test_email_guard_blocks_missing_lead_id(monkeypatch):
    from app.services import email_sender
    from app.services.email_send_guard import install_email_send_guard

    monkeypatch.setattr(email_sender, "SENDER_EMAIL", "connect@purepantryprovisions.com")
    original = email_sender.send_email
    called = {"value": False}

    def fake_send(email):
        called["value"] = True
        return email

    email_sender.send_email = fake_send
    try:
        install_email_send_guard()
        email = email_sender.OutreachEmail(
            to_email="prospect@example.com",
            to_name="Prospect",
            company="Example Co",
            subject="Test",
            body_text="Test",
        )
        result = email_sender.send_email(email)
        assert result.status == "failed"
        assert "requires lead_id" in result.error
        assert called["value"] is False
    finally:
        email_sender.send_email = original


def test_whatsapp_queue_defaults_to_no_journal():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "scripts" / "whatsapp_queue.py"
    text = source.read_text(encoding="utf-8")
    assert 'add_argument("--journal", action="store_true"' in text
    assert "journal=args.journal" in text
