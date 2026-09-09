"""
The voice adapter must refuse before it dials, not explain afterwards.

A call is the least reversible action this system takes. A wrong email is a
wrong email; a wrong call is a real phone ringing in a real shop, and in India
an unsolicited automated call to a non-consenting number is regulated, not just
rude.

So the order matters: consent is checked before configuration, and both before
any network request. These tests pin that order, and pin that the consent list
is imported from CallingAgentService rather than restated here.
"""
from __future__ import annotations

import pytest

from app.services import nuraveda_provider as nv


class Lead:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.phone = kw.get("phone", "+91-98765-43210")
        self.company = kw.get("company", "Test Traders")
        self.contact_name = kw.get("contact_name", "")
        self.city = kw.get("city", "Delhi")
        self.consent_status = kw.get("consent_status", "UNKNOWN")
        self.do_not_call = kw.get("do_not_call", False)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NURAVEDA_ENABLED", raising=False)
    assert nv.enabled() is False
    ok, why = nv.config_status()
    assert ok is False and "NURAVEDA_ENABLED" in why


def test_consent_is_checked_before_configuration(monkeypatch):
    """An unconsented lead must be refused for CONSENT, not for a missing
    secret. If config were checked first, switching the service on would
    silently change the refusal reason into an invitation."""
    monkeypatch.delenv("NURAVEDA_ENABLED", raising=False)
    result = nv.place_call(Lead(consent_status="UNKNOWN"))
    assert result.placed is False
    assert "consent gate" in result.error


def test_do_not_call_outranks_consent(monkeypatch):
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    result = nv.place_call(Lead(consent_status="EXPLICIT", do_not_call=True))
    assert result.placed is False
    assert "do_not_call" in result.error


def test_consent_vocabulary_is_imported_not_restated():
    """Two copies of an allow-list drift, and the drift shows up as calls
    nobody agreed to receive."""
    import inspect
    src = inspect.getsource(nv.may_call)
    assert "from app.services.calling_agent import CallingAgentService" in src
    assert "CALL_ALLOWED_IF" in src
    code = " ".join(line.split("#", 1)[0] for line in src.splitlines())
    assert '"IMPLIED_B2B"' not in code and "'IMPLIED_B2B'" not in code, (
        "the allowed consent states appear to be hardcoded in the adapter")


def test_consented_lead_passes_the_gate_but_still_needs_config(monkeypatch):
    """The gate must not be the only thing standing between a lead and a call —
    but it also must not block a properly consented one for the wrong reason."""
    monkeypatch.delenv("NURAVEDA_ENABLED", raising=False)
    lead = Lead(consent_status="EXPLICIT")
    allowed, why = nv.may_call(lead)
    assert allowed is True, why
    result = nv.place_call(lead)
    assert result.placed is False
    assert "NURAVEDA_ENABLED" in result.error, "should now fail on config, not consent"


def test_dry_run_never_dispatches(monkeypatch):
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")

    def explode(*a, **k):
        raise AssertionError("dry run made a network request")
    monkeypatch.setattr(nv, "_request", explode)

    result = nv.place_call(Lead(consent_status="EXPLICIT"), dry_run=True)
    assert result.placed is False
    assert "dry run" in result.error
    assert result.raw["payload"]["phone"] == "+91-98765-43210"


def test_missing_phone_is_refused():
    assert nv.place_call(Lead(phone="", consent_status="EXPLICIT")).placed is False


def test_ok_without_an_id_is_not_a_placed_call(monkeypatch):
    """A service that answers politely has not necessarily done anything.
    Treating 2xx as 'dialled' invents an outcome — the same rule the Bolna
    adapter had to learn."""
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    monkeypatch.setattr(nv, "_request", lambda *a, **k: (200, {"ok": True}))

    result = nv.place_call(Lead(consent_status="EXPLICIT"))
    assert result.placed is False, "a 200 with no call id was treated as a placed call"


def test_a_real_dispatch_is_reported_as_placed(monkeypatch):
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    monkeypatch.setattr(nv, "_request", lambda *a, **k: (
        200, {"ok": True, "id": "clx123", "scheduledAt": "2026-09-09T10:00:00Z"}))

    result = nv.place_call(Lead(consent_status="IMPLIED_B2B"))
    assert result.placed is True
    assert result.provider_call_id == "clx123"


def test_idempotency_key_is_stable_per_lead_per_day(monkeypatch):
    """The service dedupes on (shop, orderId). An unstable key would turn a
    retry into a second phone call to the same shopkeeper."""
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    seen = []
    monkeypatch.setattr(nv, "_request",
                        lambda m, p, payload=None: (seen.append(payload), (200, {"id": "a"}))[1])

    lead = Lead(id=42, consent_status="EXPLICIT")
    nv.place_call(lead)
    nv.place_call(lead)
    assert seen[0]["idempotencyKey"] == seen[1]["idempotencyKey"]
    assert "42" in seen[0]["idempotencyKey"]


def test_unreachable_service_is_a_status_not_a_crash(monkeypatch):
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    monkeypatch.setenv("NURAVEDA_URL", "http://127.0.0.1:9")  # nothing listens
    result = nv.place_call(Lead(consent_status="EXPLICIT"))
    assert result.placed is False
    assert "dispatch failed" in result.error
