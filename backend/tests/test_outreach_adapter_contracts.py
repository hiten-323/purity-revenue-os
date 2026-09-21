"""Regression contracts for outbound adapter boundaries.

These tests intentionally exercise the adapters at their provider boundary
without making a real outbound request.  They protect the architectural rule
that adapters report provider state; they do not grant permission or invent a
successful send.
"""

from dataclasses import dataclass

import pytest


@dataclass
class _Lead:
    id: int = 1
    phone: str = "+911234567890"
    company: str = "Example Co"
    contact_name: str = "Buyer"
    city: str = "Delhi"
    segment: str = "B2B"


def test_voice_router_uses_stable_daily_idempotency_key(monkeypatch):
    from app.services import voice_router

    assert voice_router.idempotency_key(_Lead()) == voice_router.idempotency_key(_Lead())


def test_voice_router_refuses_unknown_provider(monkeypatch):
    from app.services import voice_router

    monkeypatch.setenv("VOICE_PROVIDER", "unknown-provider")
    ok, detail = voice_router.config_status()
    assert ok is False
    assert "not a known provider" in detail


def test_voice_router_refuses_missing_phone(monkeypatch):
    from app.services import voice_router

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setenv("VOICE_PROVIDER", "nuraveda")
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "nuraveda: configured"))

    lead = _Lead(phone="")
    result = voice_router.place_call(lead)
    assert result.placed is False
    assert result.error == "no phone on record"


def test_voice_router_dry_run_does_not_bypass_provider_adapter(monkeypatch):
    from app.services import voice_router

    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.setenv("VOICE_PROVIDER", "nuraveda")
    monkeypatch.setattr(voice_router, "config_status", lambda: (False, "nuraveda: not configured"))

    result = voice_router.place_call(_Lead(), dry_run=True)
    assert result.placed is False
    assert "not configured" in result.error
