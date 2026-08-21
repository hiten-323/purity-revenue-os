"""
Bolna/Exotel adapter: it must refuse loudly and never invent a result.

The email path already learned this lesson twice — a send with no provider
message-id is renamed EMAIL_SENT_UNPROVEN, and consent that was defaulted
rather than recorded let the caller grant itself permission. The voice path
gets the same treatment before it is ever pointed at a real number.
"""
from __future__ import annotations

import os

import pytest

from app.services import voice_provider as vp


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("AI_CALLING_ENABLED", "BOLNA_API_KEY", "BOLNA_AGENT_ID",
              "BOLNA_FROM_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    yield


def _configure(monkeypatch):
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setenv("BOLNA_API_KEY", "test-key")
    monkeypatch.setenv("BOLNA_AGENT_ID", "agent-1")
    monkeypatch.setenv("BOLNA_FROM_NUMBER", "+915000000000")


def test_disabled_by_default():
    assert vp.enabled() is False
    ok, why = vp.config_status()
    assert ok is False and "AI_CALLING_ENABLED" in why


def test_enabled_without_credentials_names_every_missing_key(monkeypatch):
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    ok, why = vp.config_status()
    assert ok is False
    # All at once: discovering them one failed call at a time would burn
    # attempts against MAX_CALL_ATTEMPTS on leads never dialled.
    for key in ("BOLNA_API_KEY", "BOLNA_AGENT_ID", "BOLNA_FROM_NUMBER"):
        assert key in why


def test_place_call_refuses_while_disabled():
    r = vp.place_call("+919041214478")
    assert r.placed is False and r.provider_call_id == ""


def test_indian_numbers_normalised_to_e164(monkeypatch):
    _configure(monkeypatch)
    for raw in ("9041214478", "09041214478", "+91 90412 14478", "+91-90412-14478"):
        r = vp.place_call(raw, dry_run=True)
        assert r.raw["payload"]["recipient_phone_number"] == "+919041214478", raw


def test_non_indian_or_malformed_number_refused(monkeypatch):
    _configure(monkeypatch)
    for bad in ("12345", "", "abc", "+1 415 555 0100"):
        assert vp.place_call(bad).placed is False


def test_dry_run_never_dials(monkeypatch):
    _configure(monkeypatch)
    called = {"n": 0}
    monkeypatch.setattr(vp, "_post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    r = vp.place_call("+919041214478", dry_run=True)
    assert r.placed is False and called["n"] == 0


def test_accepted_without_call_id_is_not_a_placed_call(monkeypatch):
    """
    The provider saying 200 is not proof. Without an id the call cannot be
    reconciled to an outcome, and an unverifiable action must not be recorded
    as a success — the same rule the send-proof listener enforces on email.
    """
    _configure(monkeypatch)
    monkeypatch.setattr(vp, "_post", lambda *a, **k: (200, {"status": "queued"}))
    r = vp.place_call("+919041214478")
    assert r.placed is False and "no call id" in r.error


def test_provider_error_is_reported_not_swallowed(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(vp, "_post", lambda *a, **k: (401, {"error": "bad key"}))
    r = vp.place_call("+919041214478")
    assert r.placed is False and "401" in r.error


def test_successful_call_returns_provider_id(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(vp, "_post", lambda *a, **k: (201, {"call_id": "c-123"}))
    r = vp.place_call("+919041214478")
    assert r.placed is True and r.provider_call_id == "c-123"


def test_context_never_carries_empty_values(monkeypatch):
    """Only real stored values reach the agent prompt — never a blank or None."""
    _configure(monkeypatch)
    r = vp.place_call("+919041214478",
                      context={"company": "Sidhant Agencies", "contact": None, "city": ""},
                      dry_run=True)
    v = r.raw["payload"]["variables"]
    assert v == {"company": "Sidhant Agencies"}
