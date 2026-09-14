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


# --------------------------------- a mobile number is not a WhatsApp contact --

def test_unverified_number_is_not_eligible(tmp_path, monkeypatch):
    """The assumption this replaces: "it is a mobile, so it is on WhatsApp".

    NULL means nobody asked. That is not a verification and must not be
    treated as one.
    """
    from sqlalchemy.orm import sessionmaker

    from app.models.models import B2BLead, Base
    from app.services import outreach_orchestrator as o
    from conftest import memory_engine

    eng = memory_engine()
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    try:
        lead = B2BLead(company="Cafe", phone="9000000009", segment="horeca")
        lead.whatsapp_number = "9876543210"
        lead.consent_status = "EXPLICIT"          # consent is not the blocker here
        db.add(lead)
        db.commit()

        v = o.eligibility(lead, db)["whatsapp"]
        assert v["eligible"] is False
        assert "never verified" in v["reason"]

        lead.whatsapp_verified = False           # asked, and there is no account
        db.commit()
        v = o.eligibility(lead, db)["whatsapp"]
        assert v["eligible"] is False
        assert "no WhatsApp account" in v["reason"]

        lead.whatsapp_verified = True            # asked, and there is one
        db.commit()
        assert o.eligibility(lead, db)["whatsapp"]["eligible"] is True
    finally:
        db.close()
        eng.dispose()


def test_check_numbers_verifies_nothing_when_unconfigured(monkeypatch):
    monkeypatch.delenv("EVOLUTION_ENABLED", raising=False)
    assert transport.check_numbers(["9876543210"]) == {}


def test_a_number_whatsapp_says_nothing_about_stays_unasked(monkeypatch):
    """Absent from the response != confirmed absent. Only an explicit answer
    is a verification."""
    monkeypatch.setenv("EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("EVOLUTION_API_KEY", "k")
    monkeypatch.setenv("EVOLUTION_INSTANCE", "i")
    monkeypatch.delenv("EVOLUTION_INTEGRATION", raising=False)

    class _R:
        status_code = 200
        # asked about two, answered about one, and one row with no verdict
        text = ('[{"number":"919876543210","exists":true},'
                ' {"number":"919000000000"}]')

    class _C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): return _R()

    monkeypatch.setattr(transport.httpx, "Client", _C)
    out = transport.check_numbers(["9876543210", "9000000000", "9111111111"])
    assert out == {"919876543210": True}


def test_calls_are_not_restricted_to_mobiles(tmp_path, monkeypatch):
    """AI calls go to every number. A landline is perfectly callable — it is
    only WhatsApp that cannot reach one."""
    from sqlalchemy.orm import sessionmaker

    from app.models.models import B2BLead, Base
    from app.services import founder_call_pipeline as pipeline
    from app.services import preference_registry as pref
    from conftest import memory_engine

    f = tmp_path / "dnd.txt"
    f.write_text("", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None

    eng = memory_engine()
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    try:
        for phone in ("9876543210", "0172-5012345", "022-24567890"):
            lead = B2BLead(company=f"Biz {phone}", phone=phone, segment="horeca")
            db.add(lead)
            db.commit()
            ok, why = pipeline.may_place_ai_call(lead)
            assert ok is True, f"{phone} was refused: {why}"
    finally:
        db.close()
        eng.dispose()
