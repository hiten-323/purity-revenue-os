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
                 "do_not_call INTEGER, company TEXT, whatsapp_number TEXT, "
                 "consent_phone TEXT)")
    conn.executemany("INSERT INTO b2b_leads VALUES (?,?,?,?,?,?)", [
        ("+91-98765-43210", "EXPLICIT", 0, "Opted In Traders", None, None),
        ("9876500001",      "UNKNOWN",  0, "No Consent Foods", None, None),
        ("+91-98765-00002", "EXPLICIT", 1, "Asked Us To Stop", None, None),
        ("919876500003",    "IMPLIED_B2B", 0, "Implied Wholesale", None, None),
        # Opted in on the AI call for a DIFFERENT number than the one dialled.
        ("9876500004", "EXPLICIT", 0, "Read Out A Number", "9812345678", "9812345678"),
        # Consent bound to a number that is no longer the one on file.
        ("9876500005", "EXPLICIT", 0, "Number Drifted", "9876500005", "9811100000"),
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
    ok, why = wc._consent_ok("919876500003")
    assert ok is False
    assert "opt-in" in why.lower()


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
    """The connector must use whatsapp_sender's rule, not restate it. Two
    copies drift, and the drift would show up as messages nobody agreed to
    receive -- the number-binding rule was restated here once and immediately
    refused every consent recorded before consent_phone existed."""
    import inspect
    src = inspect.getsource(wc._consent_ok)
    assert "from app.services.whatsapp_sender import consent_check" in src
    assert "consent_check(lead)" in src
    assert "EXPLICIT" not in src.replace("consent_status=", ""), (
        "consent states appear to be hardcoded in the connector")


# ── consent is for a number, and the bridge honours that ────────────────────

def test_a_number_given_on_the_call_is_found_and_allowed(leads_db):
    """It lives in whatsapp_number/consent_phone, not phone. A phone-only
    lookup reported "no lead" for exactly the people who opted in this way."""
    ok, why = wc._consent_ok("+91 98123 45678")
    assert ok is True, why


def test_the_dialled_number_is_not_covered_by_a_different_numbers_opt_in(leads_db):
    ok, why = wc._consent_ok("9876500004")
    assert ok is False
    assert "number" in why.lower()


def test_consent_that_no_longer_matches_the_number_on_file_is_refused(leads_db):
    ok, why = wc._consent_ok("9876500005")
    assert ok is False
    assert "different number" in why.lower()

