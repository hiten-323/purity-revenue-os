from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _email_sender(monkeypatch):
    from app.services import email_sender

    monkeypatch.setattr(email_sender, "SENDER_PASSWORD", "test-only")
    monkeypatch.setattr(email_sender, "domain_is_deliverable", lambda _address: True)
    return email_sender


def _lead_db(lead):
    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return lead

    class Database:
        def query(self, *_args):
            return Query()

        def close(self):
            pass

    return Database()


def test_tender_approval_passes_outreach_email_and_only_marks_sent_on_success(monkeypatch):
    from app.api import endpoints
    from app.services.email_sender import OutreachEmail

    lead = SimpleNamespace(
        id=7,
        company="Acme Foods",
        contact_name="Buyer",
        email="buyer@example.com",
        proposal_text="Proposal body",
        status="QUALIFIED",
    )
    db = SimpleNamespace(
        query=lambda *_args: SimpleNamespace(
            filter=lambda *_filters: SimpleNamespace(first=lambda: lead)
        ),
        commit=lambda: None,
    )
    captured = {}

    def fake_send(email):
        captured["email"] = email
        email.status = "sent"
        email.message_id = "<test-message-id>"
        return email

    monkeypatch.setattr("app.services.email_sender.send_email", fake_send)
    monkeypatch.setenv("ZOHO_APP_PASSWORD", "test-only")

    result = endpoints.tender_approve_and_send(lead.id, db)

    assert result["status"] == "sent"
    assert isinstance(captured["email"], OutreachEmail)
    assert captured["email"].to_email == lead.email
    assert captured["email"].to_name == lead.contact_name
    assert captured["email"].subject.startswith("Tender Proposal")
    assert captured["email"].body_text == lead.proposal_text
    assert captured["email"].lead_id == lead.id
    assert lead.status == "PROPOSAL_SENT"


def test_tender_approval_does_not_claim_sent_when_adapter_refuses(monkeypatch):
    from app.api import endpoints

    lead = SimpleNamespace(
        id=8,
        company="Acme Foods",
        contact_name="Buyer",
        email="buyer@example.com",
        proposal_text="Proposal body",
        status="QUALIFIED",
    )
    db = SimpleNamespace(
        query=lambda *_args: SimpleNamespace(
            filter=lambda *_filters: SimpleNamespace(first=lambda: lead)
        ),
        commit=lambda: pytest.fail("refused email must not commit PROPOSAL_SENT"),
    )

    def refused(email):
        email.status = "failed"
        email.error = "HELD: blocked by safety gate"
        return email

    monkeypatch.setattr("app.services.email_sender.send_email", refused)
    monkeypatch.setenv("ZOHO_APP_PASSWORD", "test-only")

    with pytest.raises(HTTPException) as error:
        endpoints.tender_approve_and_send(lead.id, db)

    assert error.value.status_code == 500
    assert "blocked by safety gate" in error.value.detail
    assert lead.status == "QUALIFIED"


def test_trust_verification_failure_returns_before_smtp(monkeypatch):
    email_sender = _email_sender(monkeypatch)
    lead = SimpleNamespace(
        id=11, company="Acme", division="coffee", website="https://acme.example",
        email="buyer@example.com",
        email_verification_status="VALID",
    )
    monkeypatch.setattr(
        "app.database.database.SessionLocal", lambda: _lead_db(lead)
    )
    monkeypatch.setattr(
        "app.services.trust_promoter.may_send", lambda _lead: (True, "allowed")
    )
    monkeypatch.setattr(
        "app.services.email_verifier.verify_email",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("verifier unavailable")),
    )
    provider_calls = []
    monkeypatch.setattr(
        email_sender.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: provider_calls.append(True),
    )

    result = email_sender.send_email(email_sender.OutreachEmail(
        to_email=lead.email,
        to_name="Buyer",
        company=lead.company,
        subject="Subject",
        body_text="Body",
        lead_id=lead.id,
    ))

    assert result.status == "failed"
    assert "trust verification unavailable" in result.error
    assert provider_calls == []


def test_lead_lookup_failure_returns_before_smtp(monkeypatch):
    email_sender = _email_sender(monkeypatch)
    monkeypatch.setattr(
        "app.database.database.SessionLocal",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    provider_calls = []
    monkeypatch.setattr(
        email_sender.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: provider_calls.append(True),
    )

    result = email_sender.send_email(email_sender.OutreachEmail(
        to_email="buyer@example.com",
        to_name="Buyer",
        company="Acme",
        subject="Subject",
        body_text="Body",
        lead_id=12,
    ))

    assert result.status == "failed"
    assert "safety lookup unavailable" in result.error
    assert provider_calls == []


def test_deliverability_validation_failure_returns_before_smtp(monkeypatch):
    email_sender = _email_sender(monkeypatch)
    monkeypatch.setattr(
        "app.database.database.SessionLocal",
        lambda: (_ for _ in ()).throw(RuntimeError("deliverability unavailable")),
    )
    provider_calls = []
    monkeypatch.setattr(
        email_sender.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: provider_calls.append(True),
    )

    result = email_sender.send_email(email_sender.OutreachEmail(
        to_email="buyer@example.com",
        to_name="Buyer",
        company="Acme",
        subject="Subject",
        body_text="Body",
    ))

    assert result.status == "failed"
    assert "deliverability/account safety validation unavailable" in result.error
    assert provider_calls == []


def test_account_governance_lookup_failure_returns_before_smtp(monkeypatch):
    email_sender = _email_sender(monkeypatch)
    lead = SimpleNamespace(
        id=13,
        company="Acme",
        email="buyer@example.com",
        division="coffee",
        website="https://acme.example",
        email_verification_status="VALID",
    )
    monkeypatch.setattr(
        "app.database.database.SessionLocal", lambda: _lead_db(lead)
    )
    monkeypatch.setattr(
        "app.services.trust_promoter.may_send", lambda _lead: (True, "allowed")
    )
    monkeypatch.setattr(
        "app.services.email_verifier.verify_email",
        lambda *_args: {"status": "VALID", "mx_valid": True},
    )
    monkeypatch.setattr(
        "app.services.account_graph.account_for",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("account graph unavailable")),
    )
    provider_calls = []
    monkeypatch.setattr(
        email_sender.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: provider_calls.append(True),
    )

    result = email_sender.send_email(email_sender.OutreachEmail(
        to_email=lead.email,
        to_name="Buyer",
        company=lead.company,
        subject="Subject",
        body_text="Body",
        lead_id=lead.id,
    ))

    assert result.status == "failed"
    assert "account governance lookup unavailable" in result.error
    assert provider_calls == []
