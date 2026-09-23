from datetime import datetime
from zoneinfo import ZoneInfo

import app.services.business_hours as bh
from app.services import email_sender


IST = ZoneInfo("Asia/Kolkata")


def test_business_hours_allows_opening_and_closing(monkeypatch):
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "1")
    assert bh.is_open(datetime(2026, 9, 23, 9, 0, tzinfo=IST))
    assert bh.is_open(datetime(2026, 9, 23, 18, 0, tzinfo=IST))


def test_business_hours_blocks_evening(monkeypatch):
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "1")
    assert not bh.is_open(datetime(2026, 9, 23, 21, 54, tzinfo=IST))


def test_business_hours_uses_ist_not_utc(monkeypatch):
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "1")
    # 16:00 UTC is 21:30 IST and must be held.
    now_utc = datetime(2026, 9, 23, 16, 0, tzinfo=ZoneInfo("UTC"))
    assert not bh.is_open(now_utc)


def test_send_email_holds_after_hours_before_smtp(monkeypatch):
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "1")
    monkeypatch.setenv("SENDER_EMAIL", "connect@purepantryprovisions.com")
    monkeypatch.setenv("SENDER_NAME", "Hiten Jain | Pure Pantry Provisions")
    monkeypatch.setattr(
        "app.services.business_hours.datetime",
        type("Clock", (), {"now": staticmethod(lambda tz=None: datetime(2026, 9, 23, 21, 54, tzinfo=IST))}),
    )

    def smtp_must_not_run(*args, **kwargs):
        raise AssertionError("SMTP was contacted outside business hours")

    monkeypatch.setattr(email_sender.smtplib, "SMTP", smtp_must_not_run)
    monkeypatch.setattr(email_sender, "SENDER_PASSWORD", "test-password")
    monkeypatch.setattr(email_sender, "domain_is_deliverable", lambda *a, **k: True)

    result = email_sender.send_email(
        email_sender.OutreachEmail(
            to_email="buyer@example.com",
            to_name="Buyer",
            company="Example",
            subject="Test",
            body_text="Test",
            lead_id=None,
        )
    )
    assert result.status == "failed"
    assert "business hours" in result.error.lower()
