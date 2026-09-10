"""
One WhatsApp transport, and it is the official Cloud API.

There were three. whatsapp_sender, whatsapp_connector and
whatsapp_gateway/client each defined AISENSY_URL and each POSTed to it, so
"where does a WhatsApp message go" had three answers that only happened to
agree — and the connector reached transmission with no consent check at all
until it was fixed mid-audit.

The endpoint being a convenient module constant is what made a second and
third transport trivial to add. So the assertion is structural: no module may
name a provider endpoint of its own, and whatsapp_evolution is the only thing
that transmits.
"""
from __future__ import annotations

import ast
import os

import pytest

from app.services import whatsapp_evolution as transport

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The transport itself, plus the credential checker, which only reads an
# account status and never sends.
ALLOWED = {
    os.path.join("app", "services", "whatsapp_evolution.py"),
    os.path.join("scripts", "verify_credentials.py"),
}


def _python_files():
    for root in ("app", "scripts"):
        for dp, dn, fn in os.walk(os.path.join(BACKEND, root)):
            if "__pycache__" in dp:
                continue
            for f in fn:
                if f.endswith(".py"):
                    yield os.path.join(dp, f)
    for f in os.listdir(BACKEND):
        if f.endswith(".py"):
            yield os.path.join(BACKEND, f)


def test_no_module_hardcodes_a_whatsapp_provider_endpoint():
    offenders = []
    for path in _python_files():
        rel = os.path.relpath(path, BACKEND)
        if rel in ALLOWED:
            continue
        src = open(path, encoding="utf-8", errors="replace").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "backend.aisensy.com" in node.value:
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "these modules still name AiSensy's endpoint directly: " + ", ".join(offenders))


# ------------------------------------------------- Cloud API, not Baileys --

def test_baileys_is_refused_by_name(monkeypatch):
    """Baileys drives a real WhatsApp account over an unofficial protocol. A ban
    takes the business's own WhatsApp presence with it, not just this
    integration."""
    monkeypatch.setenv("EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("EVOLUTION_API_KEY", "k")
    monkeypatch.setenv("EVOLUTION_INSTANCE", "i")
    monkeypatch.setenv("EVOLUTION_INTEGRATION", "WHATSAPP-BAILEYS")

    ok, why = transport.config_status()
    assert ok is False
    assert "BAILEYS" in why


def test_cloud_api_is_the_default(monkeypatch):
    monkeypatch.delenv("EVOLUTION_INTEGRATION", raising=False)
    assert transport.integration() == transport.CLOUD_API


def test_an_unknown_integration_is_refused_not_guessed(monkeypatch):
    monkeypatch.setenv("EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("EVOLUTION_INTEGRATION", "carrier-pigeon")
    ok, why = transport.config_status()
    assert ok is False
    assert "not recognised" in why


# --------------------------------------------------------- fail closed --

def test_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("EVOLUTION_ENABLED", raising=False)
    assert transport.enabled() is False
    ok, why = transport.config_status()
    assert ok is False and "EVOLUTION_ENABLED" in why


def test_unconfigured_sends_nothing(monkeypatch):
    monkeypatch.delenv("EVOLUTION_ENABLED", raising=False)

    def explode(*a, **k):
        raise AssertionError("attempted a network call while unconfigured")
    monkeypatch.setattr("httpx.Client", explode)

    result = transport.send_template("9876543210", "welcome_v1")
    assert result.status == "not_configured"


# ------------------------------------------------- templates are required --

def test_free_text_first_contact_is_refused(monkeypatch):
    """Meta permits a business-initiated conversation only through an approved
    template. Refused here rather than attempted and rejected at Meta."""
    monkeypatch.setenv("EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("EVOLUTION_API_KEY", "k")
    monkeypatch.setenv("EVOLUTION_INSTANCE", "i")
    monkeypatch.delenv("EVOLUTION_INTEGRATION", raising=False)

    result = transport.send_template("9876543210", "")
    assert result.status == "blocked"
    assert "template" in result.reason


# ------------------------------------------- consent stays with the sender --

def test_the_transport_holds_no_consent_opinion():
    """A transport that re-checks consent is a second authority, and two
    authorities on one question eventually disagree."""
    import inspect
    tree = ast.parse(inspect.getsource(transport))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    for token in ("consent_status", "CONSENT_OK", "do_not_call"):
        assert token not in code, f"the transport reads {token}"


def test_the_sender_still_refuses_without_consent(monkeypatch):
    from app.services import whatsapp_sender

    monkeypatch.setenv("EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("EVOLUTION_API_KEY", "k")
    monkeypatch.setenv("EVOLUTION_INSTANCE", "i")

    class Lead:
        consent_status = "UNKNOWN"
        do_not_call = False
        status = ""
        whatsapp_number = "9876543210"
        company = "Test"
        contact_name = ""

    def explode(*a, **k):
        raise AssertionError("reached the transport without consent")
    monkeypatch.setattr(transport, "send_template", explode)

    result = whatsapp_sender.send_whatsapp(Lead(), "hello")
    assert result.status == "blocked"
    assert "opt-in" in result.reason


def test_msisdn_normalisation_matches_the_sender():
    """Two normalisers that disagree send to two different numbers."""
    for raw in ("9876543210", "09876543210", "+91 98765 43210", "919876543210"):
        assert transport.normalise_msisdn(raw) == "919876543210", raw
