"""
Founder-sent WhatsApp: a real channel, and deliberately a weaker gate.

There are two WhatsApp channels in this system and they authorise different
acts. The API channel sends from the business's own number under Meta's
opt-in policy. The manual channel is a human typing on their own phone.
Conflating them is how a system ends up cold-messaging 1300 businesses from
the number printed on its packaging.

So the tests here are mostly about the boundary:

  * the manual gate still honours every suppression the automated ones do
  * it is NOT a member of CHANNELS, so no automated plan can schedule it
  * no sender imports it, and if one ever does, this suite fails
  * the wa.me number comes from identity.msisdn, never from local stripping
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, LeadInteraction
from app.services import outreach_orchestrator as o
from conftest import memory_engine

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


def _lead(db, **kw):
    kw.setdefault("company", "Test Cafe")
    kw.setdefault("whatsapp_number", "+919876543210")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


# ------------------------------------------------------- the gate itself --

def test_ordinary_cold_lead_is_eligible(db):
    """The whole point: no consent, no verification, still messageable by hand.

    If this ever starts failing because consent is demanded, the manual
    channel has been quietly folded into the API one and there is no WhatsApp
    outreach left at all -- opt-in is 0 of 1858.
    """
    lead = _lead(db, consent_status="UNKNOWN", whatsapp_verified=None)
    ok, why = o.manual_whatsapp_ok(lead, db)
    assert ok, why


def test_do_not_call_is_refused(db):
    lead = _lead(db, do_not_call=True)
    ok, why = o.manual_whatsapp_ok(lead, db)
    assert not ok
    assert "do_not_call" in why


def test_opt_out_is_refused(db):
    lead = _lead(db, consent_status="OPT_OUT")
    ok, why = o.manual_whatsapp_ok(lead, db)
    assert not ok
    assert "OPT_OUT" in why


def test_a_business_that_said_no_is_refused(db):
    """Sending it by hand is not an answer to NOT_INTERESTED."""
    lead = _lead(db)
    db.add(LeadInteraction(lead_id=lead.id, method="phone",
                           outcome="NOT_INTERESTED", remark="said no"))
    db.commit()
    ok, why = o.manual_whatsapp_ok(lead, db)
    assert not ok
    assert "NOT_INTERESTED" in why


def test_a_number_fragment_is_refused(db):
    """A short string is not a phone number, and wa.me will happily open a
    chat with whoever does own those digits."""
    lead = _lead(db, whatsapp_number="1234")
    ok, why = o.manual_whatsapp_ok(lead, db)
    assert not ok


# ---------------------------------------------------------- the wa.me link --

def test_link_is_country_coded_not_locally_stripped(db):
    """The defect this replaced: "".join(c for c in num if c.isdigit()) turned
    a ten-digit Indian number into a ten-digit wa.me link, which wa.me reads
    as a US number. Twenty-six leads are stored without a country code."""
    _lead(db, company="Bare Ten Digits", whatsapp_number="9876543210")
    rows = o.manual_whatsapp_queue(db, limit=5, journal=False)
    assert rows, "a bare ten-digit number should still be reachable"
    number = rows[0]["number"]
    assert number.startswith("91"), number
    assert len(number) == 12, number
    assert "wa.me/91" in rows[0]["whatsapp_url"]


def test_queue_respects_suppression(db):
    _lead(db, company="Fine")
    _lead(db, company="Blocked", do_not_call=True)
    names = {r["company"] for r in o.manual_whatsapp_queue(db, limit=10, journal=False)}
    assert "Fine" in names
    assert "Blocked" not in names


def test_queue_journals_a_queued_step_not_a_sent_one(db):
    lead = _lead(db)
    o.manual_whatsapp_queue(db, limit=5, journal=True)
    rows = db.query(LeadInteraction).filter(LeadInteraction.lead_id == lead.id).all()
    assert rows, "a drafted message should leave a trace on the lead"
    assert {r.outcome for r in rows} == {"QUEUED"}, [r.outcome for r in rows]


def test_distributor_gets_the_margin_pitch(db):
    _lead(db, company="Sidhant Agencies", segment="distributor")
    row = o.manual_whatsapp_queue(db, limit=5, journal=False)[0]
    assert "28%" in row["message"]


def test_no_forbidden_claims_in_the_copy(db):
    """COMPANY_PROFILE forbids these. A WhatsApp message from the founder's
    own number is the least deniable place in the system to overclaim."""
    _lead(db, company="Anyone")
    text = o.manual_whatsapp_queue(db, limit=5, journal=False)[0]["message"].lower()
    for banned in ("iso", "turnover", "crore", "government supply", "our clients"):
        assert banned not in text, banned


# --------------------------------------------------- the boundary it keeps --

def test_manual_is_not_an_automated_channel():
    """next_touch() plans automated touches. Founder minutes are not one."""
    assert o.WHATSAPP_MANUAL not in o.CHANNELS
    assert all(step[1] != o.WHATSAPP_MANUAL for step in o.SEQUENCE)
    assert o.WHATSAPP_MANUAL not in o.GATES


def test_whatsapp_manual_is_not_an_api_gate():
    """No module that can actually transmit may import the manual gate.

    This is the eight-times-repeated defect in its newest possible form: a
    second, weaker permission rule sitting one import away from a sender that
    already has a stricter one. The manual gate says yes to 1307 leads that
    consent_check says no to. If a sender ever reaches it, cold template
    messages leave the business number and the number is what gets banned.
    """
    senders = ["app/services/whatsapp_sender.py",
               "app/services/whatsapp_aisensy.py"]
    for rel in senders:
        path = BACKEND / rel
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert "manual_whatsapp_ok" not in source, rel
        assert "manual_whatsapp_queue" not in source, rel
        assert "WHATSAPP_MANUAL" not in source, rel


def test_the_manual_gate_reuses_stop_reason_rather_than_restating_it():
    """Structural, not textual: the function body must CALL stop_reason.

    A copy of the suppression list here would drift, and the drift shows up
    as a message to a business that asked not to hear from us.
    """
    import inspect
    tree = ast.parse(inspect.getsource(o.manual_whatsapp_ok))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "stop_reason" in called


def test_no_wa_me_link_is_built_from_locally_stripped_digits():
    """Every wa.me link in the codebase must come from identity.msisdn.

    Three builders each stripped non-digits themselves. That is correct for a
    number already stored as +91..., and wrong for the 26 stored as ten
    digits: wa.me reads ten digits as a US number, so the link opens a chat
    with a stranger and the founder cannot tell from looking at it.

    The check is on the LINE that builds the URL rather than on the whole
    file, so a module may still strip digits for other reasons.
    """
    import re

    suspects = []
    for path in (BACKEND / "app").rglob("*.py"):
        lines = path.read_text(encoding="utf-8", errors="replace").split(chr(10))
        for n, line in enumerate(lines, 1):
            if "wa.me/" not in line:
                continue
            # the variable interpolated into the link, e.g. wa.me/{phone}
            m = re.search(r"wa\.me/\{(\w+)\}", line)
            if not m:
                continue
            var = m.group(1)
            # find where that variable was last assigned above this line
            for prev in range(n - 2, max(n - 40, 0), -1):
                if re.match(r"\s*" + var + r"\s*=", lines[prev]):
                    assigned = lines[prev]
                    if "msisdn" not in assigned:
                        suspects.append(f"{path.name}:{prev + 1} {assigned.strip()}")
                    break
    assert not suspects, "wa.me links not built from msisdn: " + "; ".join(suspects)
