"""
One place decides which voice agent dials.

The bug this prevents already happened once, in calling_agent.trigger_vapi_call:
the configuration gate validated BOLNA credentials and the code below it POSTed
to VAPI. Nothing caught it because no single place owned the answer to "which
provider are we actually using". These tests pin that the answer now has one
owner, that an unknown name is refused rather than guessed, and that the router
never grows a consent opinion of its own.
"""
from __future__ import annotations

import pytest

from app.services import voice_router as vr


class Lead:
    def __init__(self, **kw):
        self.id = kw.get("id", 7)
        self.phone = kw.get("phone", "+91-98765-43210")
        self.company = kw.get("company", "Test Traders")
        self.contact_name = kw.get("contact_name", "Anil")
        self.city = kw.get("city", "Ludhiana")
        self.segment = kw.get("segment", "horeca")
        self.consent_status = kw.get("consent_status", "UNKNOWN")
        self.do_not_call = kw.get("do_not_call", False)


def test_defaults_to_nuraveda(monkeypatch):
    monkeypatch.delenv("VOICE_PROVIDER", raising=False)
    assert vr.active() == vr.NURAVEDA


def test_unknown_provider_is_refused_not_guessed(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "twilio")
    ok, why = vr.config_status()
    assert ok is False
    assert "not a known provider" in why


def test_config_status_reports_the_active_provider_only(monkeypatch):
    """A green light for a provider we are NOT using is worse than no light.

    Bolna was removed, so there is nothing to leak today. The assertion stays
    because the failure it guards — a config gate validating one provider while
    the code dials another — is what trigger_vapi_call actually did.
    """
    monkeypatch.setenv("VOICE_PROVIDER", "nuraveda")
    monkeypatch.delenv("NURAVEDA_ENABLED", raising=False)
    ok, why = vr.config_status()
    assert ok is False
    assert why.startswith("nuraveda:")
    assert "BOLNA" not in why


def test_the_dial_reaches_the_adapter(monkeypatch):
    """Not just the label -- the dial has to actually land in nuraveda."""
    from app.services import nuraveda_provider as nvm

    monkeypatch.setenv("VOICE_PROVIDER", "nuraveda")
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    calls = []
    monkeypatch.setattr(nvm, "_request",
                        lambda m, p, payload=None: (calls.append(payload), (200, {"id": "n1"}))[1])

    result = vr.place_call(Lead())
    assert result.placed is True
    assert result.provider_call_id == "n1"
    assert calls, "nuraveda adapter was not reached"


def test_idempotency_key_is_stable_per_lead_per_day():
    lead = Lead(id=42)
    assert vr.idempotency_key(lead) == vr.idempotency_key(lead)
    assert "42" in vr.idempotency_key(lead)


def test_router_passes_the_key_through(monkeypatch):
    from app.services import nuraveda_provider as nvm

    monkeypatch.setenv("VOICE_PROVIDER", "nuraveda")
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    seen = []
    monkeypatch.setattr(nvm, "_request",
                        lambda m, p, payload=None: (seen.append(payload), (200, {"id": "a"}))[1])

    lead = Lead(id=42)
    vr.place_call(lead)
    assert seen[0]["idempotencyKey"] == vr.idempotency_key(lead)


def test_no_phone_is_refused_before_dialling(monkeypatch):
    monkeypatch.setenv("VOICE_PROVIDER", "nuraveda")
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")

    result = vr.place_call(Lead(phone=""))
    assert result.placed is False
    assert "no phone" in result.error


def test_router_holds_no_consent_opinion():
    """Permission belongs to founder_call_pipeline (cold) or to
    check_eligibility (consented). A third opinion is a future disagreement."""
    import inspect
    src = inspect.getsource(vr)
    body = "\n".join(line for line in src.splitlines()
                     if not line.strip().startswith("#"))
    # the module docstring may discuss consent; executable code must not read it
    code = body.split('"""')[-1]
    assert "consent_status" not in code
    assert "CALL_ALLOWED_IF" not in code
