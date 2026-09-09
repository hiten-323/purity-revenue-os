"""
The voice adapter dials. It does not decide who may be dialled.

That split changed. This adapter used to take a lead and run may_call() on it
before dispatching, which read as defence in depth and was really a second
authority: once founder_call_pipeline owns permission for the cold
qualification call, an adapter that re-checks consent_status refuses every call
the pipeline just authorised -- every lead is UNKNOWN -- and two gates disagree
about one dial.

So place_call() now takes a PHONE. With no lead in scope it cannot form an
opinion about permission, the same way scrapling_dry_run cannot write because
it never receives a db handle. Structure beats a rule someone has to remember.

may_call() survives as an exported helper for the consented path, and the test
that it imports its vocabulary rather than restating it survives with it --
that one has caught real drift before.
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


# ------------------------------------------------------------ configuration --

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("NURAVEDA_ENABLED", raising=False)
    assert nv.enabled() is False
    ok, why = nv.config_status()
    assert ok is False and "NURAVEDA_ENABLED" in why


def test_unconfigured_refuses_before_any_network_request(monkeypatch):
    monkeypatch.delenv("NURAVEDA_ENABLED", raising=False)

    def explode(*a, **k):
        raise AssertionError("dialled while unconfigured")
    monkeypatch.setattr(nv, "_request", explode)

    result = nv.place_call("+91-98765-43210")
    assert result.placed is False
    assert "NURAVEDA_ENABLED" in result.error


# ------------------------------------------- the adapter cannot judge consent --

def test_place_call_takes_a_phone_not_a_lead():
    """Pinned deliberately: a lead-shaped signature is what let the adapter
    grow a consent opinion of its own."""
    import inspect
    params = list(inspect.signature(nv.place_call).parameters)
    assert params[0] == "phone", (
        "place_call must take a phone; a lead in scope invites a second "
        "consent authority")


def test_adapter_does_not_re_check_consent(monkeypatch):
    """A phone belonging to an UNKNOWN lead still dials, because permission was
    already decided upstream by founder_call_pipeline."""
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    monkeypatch.setattr(nv, "_request", lambda *a, **k: (200, {"id": "clx1"}))

    result = nv.place_call("+91-98765-43210")
    assert result.placed is True


# ------------------------------------ may_call still owns the consented path --

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


def test_may_call_refuses_an_unconsented_lead():
    allowed, why = nv.may_call(Lead(consent_status="UNKNOWN"))
    assert allowed is False
    assert "no consent on record" in why


def test_do_not_call_outranks_consent():
    allowed, why = nv.may_call(Lead(consent_status="EXPLICIT", do_not_call=True))
    assert allowed is False
    assert "do_not_call" in why


def test_may_call_allows_a_consented_lead():
    allowed, why = nv.may_call(Lead(consent_status="EXPLICIT"))
    assert allowed is True, why


# ------------------------------------------------------------- dispatch --

def test_dry_run_never_dispatches(monkeypatch):
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")

    def explode(*a, **k):
        raise AssertionError("dry run made a network request")
    monkeypatch.setattr(nv, "_request", explode)

    result = nv.place_call("+91-98765-43210", dry_run=True)
    assert result.placed is False
    assert "dry run" in result.error
    assert result.raw["payload"]["phone"] == "+91-98765-43210"


def test_missing_phone_is_refused():
    assert nv.place_call("").placed is False


def test_ok_without_an_id_is_not_a_placed_call(monkeypatch):
    """A service that answers politely has not necessarily done anything.
    Treating 2xx as 'dialled' invents an outcome -- the same rule the Bolna
    adapter had to learn."""
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    monkeypatch.setattr(nv, "_request", lambda *a, **k: (200, {"ok": True}))

    result = nv.place_call("+91-98765-43210")
    assert result.placed is False, "a 200 with no call id was treated as a placed call"


def test_a_real_dispatch_is_reported_as_placed(monkeypatch):
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    monkeypatch.setattr(nv, "_request", lambda *a, **k: (
        200, {"ok": True, "id": "clx123", "scheduledAt": "2026-09-09T10:00:00Z"}))

    result = nv.place_call("+91-98765-43210")
    assert result.placed is True
    assert result.provider_call_id == "clx123"


def test_caller_supplied_idempotency_key_is_used(monkeypatch):
    """The service dedupes on it, so an unstable key turns a retry into a
    second phone call to the same shopkeeper. The KEY now belongs to
    voice_router, which knows what 'the same call' means; the adapter just
    forwards it."""
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    seen = []
    monkeypatch.setattr(nv, "_request",
                        lambda m, p, payload=None: (seen.append(payload), (200, {"id": "a"}))[1])

    nv.place_call("+91-98765-43210", idempotency_key="purity-lead-42-20260909")
    assert seen[0]["idempotencyKey"] == "purity-lead-42-20260909"


def test_a_bare_call_still_dedupes_within_a_day(monkeypatch):
    """No key supplied must not mean no key -- that would let a retry ring
    twice."""
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    seen = []
    monkeypatch.setattr(nv, "_request",
                        lambda m, p, payload=None: (seen.append(payload), (200, {"id": "a"}))[1])

    nv.place_call("+91 98765 43210")
    nv.place_call("098765-43210")        # same number, written differently
    assert seen[0]["idempotencyKey"] == seen[1]["idempotencyKey"]


def test_unreachable_service_is_a_status_not_a_crash(monkeypatch):
    monkeypatch.setenv("NURAVEDA_ENABLED", "1")
    monkeypatch.setenv("NURAVEDA_TOOL_SECRET", "x")
    monkeypatch.setenv("NURAVEDA_URL", "http://127.0.0.1:9")  # nothing listens

    result = nv.place_call("+91-98765-43210")
    assert result.placed is False
    assert result.error
