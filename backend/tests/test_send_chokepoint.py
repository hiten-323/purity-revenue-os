"""
Exactly one path may reach a prospect's inbox.

email_sender.send_email applies Gate A (email_trust in MAY_SEND and
email_confidence >= 40), Gate A2 (account suppression, per-company frequency
cap), Gate B (provider throttling and volume), and writes the send-proof event
carrying a real message-id. Everything the system knows about who may be
emailed lives behind that one function.

tender_auto_pricer opened its own smtplib.SMTP("smtp.zoho.in", 587) and
sendmail()'d straight to lead.email, with send_emails=True as the default and
an unauthenticated API route calling it. A discount offer could reach an
unverified, suppressed, or recently-contacted prospect, leaving no record that
would let anyone tell afterwards.

email_sender's own comment predicted this: "Seven call sites reach SMTP and
five write EMAIL_SENT. Fixing them one by one is what left two paths ungated
the last time."

So this does not fix them one by one. It asserts the property.
"""
from __future__ import annotations

import ast
import os

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The governed sender is allowed to talk SMTP -- that is its job.
# founder_brief mails a fixed internal address (the founder's own inbox) and
# never a prospect, so it is not an outbound-governance path. Any OTHER module
# opening SMTP is a second way to reach a buyer.
ALLOWED = {
    os.path.join("app", "services", "email_sender.py"),
    os.path.join("app", "services", "founder_brief.py"),
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


def _rel(path):
    return os.path.relpath(path, BACKEND)


def test_nothing_but_the_governed_sender_transmits_mail():
    """The property is TRANSMITTING, not connecting.

    scripts/verify_credentials.py opens smtplib.SMTP_SSL and calls login() to
    check the Zoho password is still valid. It never calls sendmail, so it
    cannot reach a prospect and is not a governance bypass. An earlier version
    of this test flagged it, which would have taught the next person to
    allow-list files instead of asking what the file actually does.

    Executable calls only -- a docstring describing the old code is fine.
    """
    offenders = []
    for path in _python_files():
        rel = _rel(path)
        if rel in ALLOWED:
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("sendmail", "send_message"):
                    offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "these modules transmit mail directly and therefore bypass Gate A, "
        "Gate A2, Gate B and send-proof: " + ", ".join(offenders))


def test_founder_brief_only_mails_the_founder():
    """It is on the allow-list because of WHO it mails. Pin that, so the
    exemption cannot quietly widen into prospect mail."""
    src = open(os.path.join(BACKEND, "app", "services", "founder_brief.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)

    recipients = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "recipient" for t in n.targets)
    ]
    assert recipients, "no `recipient` assignment found — has this file changed shape?"
    for r in recipients:
        assert isinstance(r, ast.Constant) and isinstance(r.value, str), (
            "founder_brief's recipient is no longer a fixed literal address; "
            "if it can now be a lead, it must go through email_sender")
    assert "lead.email" not in src, (
        "founder_brief references lead.email — it is exempt only because it "
        "mails a fixed internal address")


def test_tender_pricer_routes_through_send_email():
    from app.services import tender_auto_pricer as t
    import inspect

    src = inspect.getsource(t._send_nudge_email)
    body = src.split('"""')[-1]          # skip the docstring, which names the old code
    assert "send_email(" in body
    assert "sendmail(" not in body
    assert "lead_id=lead.id" in body, (
        "without lead_id the gate looks the lead up as None and passes silently")
