"""
The WhatsApp connector must not become a second authority on who may be messaged.

As merged, /api/v1/whatsapp/send checked the connector secret, the API key and
idempotency — then posted to AiSensy. It never read b2b_leads, so it could
message a business whose consent_status was UNKNOWN or who was marked
do-not-call, entirely bypassing whatsapp_sender's gate.

The secret proves a request came from our own integration. It says nothing
about whether the business at the other end agreed to be contacted. Those are
different questions and the connector now asks both.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

import whatsapp_connector as wc


@pytest.fixture
def leads_db(monkeypatch):
    """A minimal lead table in the shapes the real one actually stores."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE b2b_leads (phone TEXT, consent_status TEXT, "
                 "do_not_call INTEGER, company TEXT)")
    conn.executemany("INSERT INTO b2b_leads VALUES (?,?,?,?)", [
        ("+91-98765-43210", "EXPLICIT", 0, "Opted In Traders"),
        ("9876500001",      "UNKNOWN",  0, "No Consent Foods"),
        ("+91-98765-00002", "EXPLICIT", 1, "Asked Us To Stop"),
        ("919876500003",    "IMPLIED_B2B", 0, "Implied Wholesale"),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(wc, "LEADS_DB_PATH", path)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def test_opted_in_lead_is_allowed(leads_db):
    ok, why = wc._consent_ok("+91-98765-43210")
    assert ok is True, why


def test_phone_format_does_not_decide_consent(leads_db):
    """The lead table stores +91-98765-43210, 919876500003 and bare 10-digit
    forms. An exact-string match would find nothing and read as 'no such lead',
    which is a refusal for the wrong reason — right answer, broken test."""
    for form in ("9876543210", "+919876543210", "91 98765 43210", "098765-43210"):
        ok, _ = wc._consent_ok(form)
        assert ok is True, f"{form} failed to match the opted-in lead"
    ok, _ = wc._consent_ok("919876500003")
    assert ok is True, "IMPLIED_B2B lead stored with 91 prefix was not matched"


def test_unknown_consent_is_refused(leads_db):
    ok, why = wc._consent_ok("9876500001")
    assert ok is False
    assert "opt-in" in why.lower()


def test_do_not_call_outranks_explicit_consent(leads_db):
    """Consent granted then withdrawn must lose. This lead has EXPLICIT consent
    AND do_not_call set; the refusal has to win."""
    ok, why = wc._consent_ok("+91-98765-00002")
    assert ok is False
    assert "do-not-call" in why.lower()


def test_unknown_number_is_refused(leads_db):
    ok, why = wc._consent_ok("+91-90000-00000")
    assert ok is False
    assert "no lead" in why.lower()


def test_malformed_destination_is_refused(leads_db):
    for bad in ("12345", "", "abcdefghij"):
        ok, _ = wc._consent_ok(bad)
        assert ok is False, bad


def test_it_fails_closed_when_the_rule_is_unreachable(leads_db, monkeypatch):
    """If the consent rule cannot be imported, refuse. A send path that cannot
    reach the rule has not satisfied it — the opposite default is how a guard
    silently stops guarding."""
    monkeypatch.setattr(wc, "LEADS_DB_PATH", "/nonexistent/nowhere.db")
    ok, why = wc._consent_ok("+91-98765-43210")
    assert ok is False
    assert "refusing to send" in why.lower()


def test_consent_vocabulary_is_not_redefined_here():
    """The connector must import CONSENT_OK, not restate it. Two copies drift,
    and the drift would show up as messages nobody agreed to receive."""
    import inspect
    src = inspect.getsource(wc._consent_ok)
    assert "from app.services.whatsapp_sender import CONSENT_OK" in src
    assert "EXPLICIT" not in src.replace("consent_status=", ""), (
        "consent states appear to be hardcoded in the connector")
