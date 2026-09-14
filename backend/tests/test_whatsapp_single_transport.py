"""
One WhatsApp transport, and it is the one the business number already lives on.

There were three. whatsapp_sender, whatsapp_connector and
whatsapp_gateway/client each defined AISENSY_URL and each POSTed to it, so
"where does a WhatsApp message go" had three answers that only happened to
agree — and the connector reached transmission with no consent check at all
until it was fixed mid-audit.

Then there was one, and it was Evolution driving Meta's Cloud API. It is
AiSensy again now, for a reason that is not preference: a phone number belongs
to exactly one WhatsApp Business Account, this number's WABA is under AiSensy,
and seven Live campaigns carrying real orders hang off it.

The transport changed twice. The property being asserted has not: no module
may name a provider endpoint of its own, exactly one module transmits, and it
holds no opinion about who may be messaged.
"""
from __future__ import annotations

import ast
import os

import pytest

from app.services import whatsapp_aisensy as transport

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The transport itself, plus the credential checker, which only reads an
# account status and never sends.
ALLOWED = {
    os.path.join("app", "services", "whatsapp_aisensy.py"),
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


def test_only_one_module_builds_a_whatsapp_request():
    """The gateway used to build its own URL and payload from the transport's
    base_url. Borrowing an endpoint is not sharing an implementation: it was
    still a second request with its own idea of the payload, one edit from
    drifting. It must delegate."""
    path = os.path.join(BACKEND, "app", "services", "whatsapp_gateway", "client.py")
    src = open(path, encoding="utf-8").read()
    assert "send_template" in src, "the gateway no longer delegates to the transport"

    # Structural, not textual: `destination` is a fine parameter name. What
    # must not exist is a dict literal carrying the provider's payload keys.
    payload_keys = {"apiKey", "campaignName", "templateParams"}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        assert not (keys & payload_keys), (
            f"the gateway builds its own AiSensy payload again at line "
            f"{node.lineno}: {sorted(keys & payload_keys)}")


# --------------------------------------------------------- fail closed --

def test_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("AISENSY_ENABLED", raising=False)
    assert transport.enabled() is False
    ok, why = transport.config_status()
    assert ok is False and "AISENSY_ENABLED" in why


def test_unconfigured_sends_nothing(monkeypatch):
    monkeypatch.delenv("AISENSY_ENABLED", raising=False)

    def explode(*a, **k):
        raise AssertionError("attempted a network call while unconfigured")
    monkeypatch.setattr("httpx.Client", explode)

    result = transport.send_template("9876543210", "welcome_v1")
    assert result.status == "not_configured"


def test_a_masked_key_is_named_as_the_real_mistake(monkeypatch):
    """32 bullet characters copied from a dashboard instead of the secret.
    This has happened twice on this project with a different credential, and
    the raw failure names a codec rather than the mistake."""
    monkeypatch.setenv("AISENSY_ENABLED", "1")
    monkeypatch.setenv("AISENSY_API_KEY", "•" * 32)
    ok, why = transport.config_status()
    assert ok is False
    assert "masked" in why


# ------------------------------------------------- campaigns are required --

def test_free_text_first_contact_is_refused(monkeypatch):
    """Meta permits a business-initiated conversation only through an approved
    template. AiSensy reaches one through a Live campaign; with neither named,
    this is refused here rather than attempted and rejected."""
    monkeypatch.setenv("AISENSY_ENABLED", "1")
    monkeypatch.setenv("AISENSY_API_KEY", "k")
    monkeypatch.delenv("WHATSAPP_TEMPLATE", raising=False)
    monkeypatch.delenv("AISENSY_CAMPAIGN_NAME", raising=False)

    ok, why = transport.config_status()
    assert ok is False
    assert "LIVE" in why or "Live" in why


# ------------------------------- a polite 200 is not necessarily a message --

def _fake_client(status=200, text="{}", headers=None):
    class _R:
        status_code = status
        pass
    _R.text = text
    _R.headers = headers or {}

    class _C:
        def post(self, *a, **k):
            return _R()
    return _C()


def test_2xx_without_a_message_id_is_accepted_not_failed(monkeypatch):
    """The Evolution transport treated a 2xx with no id as a failure, which was
    right there: it promised one. AiSensy's documented success contract is only
    "status 200". Calling that a failure would report every real send as
    broken — a bug this codebase has already shipped once."""
    monkeypatch.setenv("AISENSY_ENABLED", "1")
    monkeypatch.setenv("AISENSY_API_KEY", "k")
    monkeypatch.setenv("WHATSAPP_TEMPLATE", "Purity Outreach")

    r = transport.send_template("9876543210", "Purity Outreach",
                                client=_fake_client(200, '{"success":true}'))
    assert r.status == "sent"
    assert r.provider_accepted is True
    assert r.delivery_confirmed is False
    assert r.message_id == ""
    assert "cannot be matched" in r.reason


def test_a_message_id_is_used_when_the_provider_gives_one(monkeypatch):
    monkeypatch.setenv("AISENSY_ENABLED", "1")
    monkeypatch.setenv("AISENSY_API_KEY", "k")
    monkeypatch.setenv("WHATSAPP_TEMPLATE", "Purity Outreach")

    for body in ('{"messageId":"abc123"}', '{"data":{"id":"abc123"}}',
                 '{"wamid":"abc123"}'):
        r = transport.send_template("9876543210", "Purity Outreach",
                                    client=_fake_client(200, body))
        assert r.message_id == "abc123", body


def test_non_2xx_is_a_failure_with_the_status_named(monkeypatch):
    monkeypatch.setenv("AISENSY_ENABLED", "1")
    monkeypatch.setenv("AISENSY_API_KEY", "k")
    monkeypatch.setenv("WHATSAPP_TEMPLATE", "Purity Outreach")

    r = transport.send_template("9876543210", "Purity Outreach",
                                client=_fake_client(400, '{"error":"campaign not live"}'))
    assert r.status == "failed"
    assert "400" in r.reason
    assert r.provider_accepted is False


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

    monkeypatch.setenv("AISENSY_ENABLED", "1")
    monkeypatch.setenv("AISENSY_API_KEY", "k")
    monkeypatch.setenv("WHATSAPP_TEMPLATE", "Purity Outreach")

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


# ---------------------------------- verification: NULL is not the same as no --

def test_null_verification_no_longer_blocks_but_a_negative_still_does(tmp_path):
    """whatsapp_verified was set by Evolution's /chat/whatsappNumbers, a
    WhatsApp-Web capability. Meta exposes no equivalent — enumerating its users
    is exactly what it will not allow — so nobody can ask any more.

    NULL therefore means "unasked, and unaskable", which must not refuse all
    1,307 leads forever. False still means WhatsApp was asked and said no.
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

        assert lead.whatsapp_verified is None
        assert o.eligibility(lead, db)["whatsapp"]["eligible"] is True

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


def test_consent_is_still_the_binding_gate(tmp_path):
    """Relaxing verification must not have relaxed anything else. A lead with
    a number and no opt-in is still refused."""
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
        lead.consent_status = "UNKNOWN"
        db.add(lead)
        db.commit()

        v = o.eligibility(lead, db)["whatsapp"]
        assert v["eligible"] is False
        assert "opt-in" in v["reason"]
    finally:
        db.close()
        eng.dispose()


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
