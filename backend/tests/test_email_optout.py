"""
Every prospect email must carry a way out, and that way out must work.

Two halves, and the second is the one that matters. Putting an opt-out
sentence in the footer is easy; the sentence names a specific reply, and if
nothing in the pipeline acts on that reply the footer is a lie told at scale.
So the round-trip test feeds the exact word the footer asks for back through
reply_intelligence and asserts it reaches SUPPRESS_ACCOUNT, which is what
decision_engine reads as suppression.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.services import email_sender as es


class _CapturedSMTP:
    """Stands in for smtplib.SMTP and keeps the message instead of sending."""

    sent: list[str] = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def sendmail(self, sender, to, body):
        _CapturedSMTP.sent.append(body)


class _NoDB:
    """send_email opens a session for its governance gates. These tests are
    about the message that gets built, so no database is touched at all."""

    def close(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    import types

    import app.database.database as dbmod
    from app.services import deliverability as deliv

    _CapturedSMTP.sent = []
    monkeypatch.setattr(es.smtplib, "SMTP", _CapturedSMTP)
    monkeypatch.setattr(es, "SENDER_PASSWORD", "test-password")
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "0")
    # The NXDOMAIN gate does a real DNS lookup; this suite must not.
    monkeypatch.setattr(es, "domain_is_deliverable", lambda addr: True)
    # Gate B (volume/pace/provider health) fails closed without a database,
    # which is correct and not what these tests are measuring.
    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _NoDB())
    monkeypatch.setattr(
        deliv, "check_send_allowed",
        lambda db: types.SimpleNamespace(allowed=True, reason="stubbed for test"))
    return _CapturedSMTP


def _prospect(**kw):
    kw.setdefault("to_email", "buyer@examplecafe.in")
    kw.setdefault("to_name", "Buyer")
    kw.setdefault("company", "Example Cafe")
    kw.setdefault("subject", "Coffee for Example Cafe")
    kw.setdefault("body_text", "Hi Buyer,\n\nWe supply Purity Beans.")
    return es.OutreachEmail(**kw)


def test_prospect_mail_carries_the_optout_line_and_header(captured):
    result = es.send_email(_prospect())

    assert result.status == "sent", result.error
    raw = captured.sent[0]
    assert "List-Unsubscribe" in raw
    assert f"mailto:{es.SENDER_EMAIL}" in raw
    assert es.OPT_OUT_SENTENCE in result.body_text


def test_the_optout_survives_a_caller_that_builds_its_own_html(captured):
    """Three callers construct an OutreachEmail directly rather than through
    build_outreach_email. The footer is applied at the send chokepoint, so
    their HTML gets it too."""
    email = _prospect(body_html="<html><body>Hi Buyer</body></html>")
    es.send_email(email)

    assert es.OPT_OUT_SENTENCE in email.body_html
    assert email.body_html.endswith("</body></html>")


def test_mail_to_the_founders_own_inbox_is_left_alone(captured):
    """Draft previews and controlled verification sends go to SENDER_EMAIL.
    An opt-out line there would be quoted into a real email later."""
    email = _prospect(to_email=es.SENDER_EMAIL, to_name="Hiten")
    es.send_email(email)

    raw = captured.sent[0]
    assert "List-Unsubscribe" not in raw
    assert es.OPT_OUT_SENTENCE not in email.body_text


def test_the_line_is_added_once_not_once_per_send(captured):
    """A retry, or a body that already carries the sentence, must not stack
    footers."""
    email = _prospect()
    es.send_email(email)
    es.send_email(email)

    assert email.body_text.count(es.OPT_OUT_SENTENCE) == 1


def test_the_word_we_ask_for_actually_suppresses_the_account():
    """The round trip. The footer asks the recipient to reply "unsubscribe";
    reply_intelligence must classify that as DO_NOT_CONTACT and route it to
    SUPPRESS_ACCOUNT, which is the payload decision_engine._gather_facts reads
    as suppression. If this ever drifts, the footer is promising something the
    system does not do."""
    from app.services import reply_intelligence as ri

    assert "unsubscribe" in es.OPT_OUT_SENTENCE.lower(), (
        "the footer no longer names the reply the pipeline honours")

    r = ri.analyse("Re: Coffee for Example Cafe", "unsubscribe", {})

    assert "DO_NOT_CONTACT" in [i["intent"] for i in r["intents"]]
    assert r["next_action"] == "SUPPRESS_ACCOUNT"
