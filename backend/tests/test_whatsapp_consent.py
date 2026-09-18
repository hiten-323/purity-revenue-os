"""
How WhatsApp consent is acquired, and everything that must not create it.

The channel has no lawful way to open except a business saying so. Email is
the only route that can ask at scale, so this pins that route end to end:
the ask goes out, the reply names the channel, consent is recorded with the
words used, bound to the number it was given for.

The negative tests matter more than the positive one. Every item in the
"does not count" list below is something that would be convenient to treat as
permission, and treating any of them that way is how a business gets messaged
without having agreed to it.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, WorkflowEvent
from app.services import whatsapp_consent
from app.services import reply_intelligence as ri
from app.services.email_sender import WHATSAPP_ASK_SENTENCE, whatsapp_ask
from app.services.whatsapp_sender import consent_check
from conftest import memory_engine


@pytest.fixture
def db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def _lead(db, **kw):
    kw.setdefault("company", "Consent Test Cafe")
    kw.setdefault("email", "owner@consenttest.in")
    kw.setdefault("whatsapp_number", "+919876543210")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


def _events(db, lead, kind):
    return (db.query(WorkflowEvent)
            .filter(WorkflowEvent.lead_id == lead.id,
                    WorkflowEvent.event_type == kind).all())


# ── the email route ───────────────────────────────────────────────────────────

def test_a_reply_naming_whatsapp_records_consent_with_the_words_used(db):
    lead = _lead(db)
    body = "Yes please send it on WhatsApp, that is easier for us."

    ri.process(lead, db, "Re: Coffee for Consent Test Cafe", body,
               {"Message-ID": "<reply-1@consenttest.in>"}, "owner@consenttest.in")

    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_source == "EMAIL_REPLY_WHATSAPP_REQUEST"
    assert lead.consent_phone == "+919876543210"

    ev = _events(db, lead, "WHATSAPP_CONSENT_RECORDED")
    assert len(ev) == 1
    assert body in ev[0].payload["evidence"], "the business's own words are the evidence"
    assert ev[0].payload["message_id"] == "<reply-1@consenttest.in>"

    # and the commitment decision_engine reads
    assert [e.payload["action"] for e in _events(db, lead, "NEXT_ACTION_SET")] == ["SEND_WHATSAPP"]
    assert consent_check(lead)[0] is True


@pytest.mark.parametrize("body", [
    "Yes, WhatsApp is fine.",
    "please share on whatsapp",
    "ok whatsapp",
    "Send it over WhatsApp please",
    "whatsapp works better for me",
])
def test_the_phrasings_the_ask_invites_are_all_recognised(db, body):
    lead = _lead(db)
    ri.process(lead, db, "Re: Coffee", body, {}, "owner@consenttest.in")
    assert lead.consent_status == "EXPLICIT", f"not recognised: {body!r}"


# ── what must never become consent ───────────────────────────────────────────

@pytest.mark.parametrize("body,why", [
    ("Yes please.", "a bare yes names no channel and proves nothing on its own"),
    ("Thanks, noted.", "acknowledgement is not permission"),
    ("Sure, send me the catalogue.", "agreeing to information is not agreeing to a channel"),
    ("We are on WhatsApp.", "stating the channel exists is not permission to use it"),
    ("Our WhatsApp number is 9876543210.", "publishing a number is not an invitation"),
    ("Please remove me.", "an opt-out must never read as an opt-in"),
    ("Not interested.", "a refusal is not consent"),
])
def test_these_replies_do_not_create_consent(db, body, why):
    lead = _lead(db)
    ri.process(lead, db, "Re: Coffee", body, {}, "owner@consenttest.in")

    assert (lead.consent_status or "UNKNOWN").upper() != "EXPLICIT", why
    assert _events(db, lead, "WHATSAPP_CONSENT_RECORDED") == []


def test_a_machine_reply_mentioning_whatsapp_is_not_a_business_agreeing(db):
    """An auto-responder whose signature block says "WhatsApp us on ..." is
    the exact failure this module's human check exists for."""
    lead = _lead(db)
    ri.process(
        lead, db, "Automatic reply: Out of office",
        "I am out of office until 5 Oct. For urgent matters WhatsApp me on 98765 43210.",
        {"Auto-Submitted": "auto-replied"}, "owner@consenttest.in")

    assert (lead.consent_status or "UNKNOWN").upper() != "EXPLICIT"
    assert _events(db, lead, "WHATSAPP_CONSENT_RECORDED") == []


def test_consent_is_not_granted_when_there_is_no_number_to_bind_it_to(db):
    """They said yes, but to what? Recorded as a fact for a human to chase,
    never as consent — a blank binding would attach this opt-in to whatever
    number enrichment discovers next."""
    lead = _lead(db, whatsapp_number=None, phone=None)

    ri.process(lead, db, "Re: Coffee", "yes, send on whatsapp", {}, "owner@consenttest.in")

    assert (lead.consent_status or "UNKNOWN").upper() != "EXPLICIT"
    assert len(_events(db, lead, "WHATSAPP_CONSENT_UNBOUND")) == 1
    assert _events(db, lead, "WHATSAPP_CONSENT_RECORDED") == []


# ── the writer itself ────────────────────────────────────────────────────────

def test_an_unrecognised_source_is_refused(db):
    lead = _lead(db)
    with pytest.raises(ValueError, match="not a recognised consent source"):
        whatsapp_consent.record(lead, db, source="SEEMED_KEEN", evidence="they sounded happy")


def test_consent_without_evidence_is_refused(db):
    lead = _lead(db)
    with pytest.raises(ValueError, match="requires evidence"):
        whatsapp_consent.record(lead, db, source="FOUNDER_CALL", evidence="   ")


def test_recording_the_same_consent_twice_is_idempotent(db):
    lead = _lead(db)
    first = whatsapp_consent.record(lead, db, source="FOUNDER_CALL",
                                    evidence="said yes on the call")
    second = whatsapp_consent.record(lead, db, source="FOUNDER_CALL",
                                     evidence="said yes on the call")
    db.commit()

    assert first["recorded"] is True and second["recorded"] is False
    assert len(_events(db, lead, "WHATSAPP_CONSENT_RECORDED")) == 1


def test_the_ai_call_path_writes_through_the_same_writer(db, monkeypatch):
    """founder_call_pipeline used to set the consent fields itself. Same
    outcome, one author."""
    from app.services import founder_call_pipeline as p

    lead = _lead(db, phone="+919876543210", segment="cafe")
    p.record_ai_outcome(lead, db, "WHATSAPP_OPT_IN",
                        summary="asked us to send the range on WhatsApp")
    db.commit()

    assert lead.consent_source == "AI_CALL_WHATSAPP_REQUEST"
    assert lead.consent_phone == "+919876543210"
    ev = _events(db, lead, "WHATSAPP_CONSENT_RECORDED")
    assert len(ev) == 1
    assert "WhatsApp" in ev[0].payload["evidence"]


# ── the outbound ask ─────────────────────────────────────────────────────────

def test_the_ask_goes_only_to_leads_it_could_be_acted_on_for(db):
    reachable = _lead(db)
    assert WHATSAPP_ASK_SENTENCE in whatsapp_ask(reachable)

    no_number = _lead(db, company="No Number", whatsapp_number=None, phone=None)
    assert whatsapp_ask(no_number) == "", "nothing to message"

    already = _lead(db, company="Already Consented", consent_status="EXPLICIT")
    assert whatsapp_ask(already) == "", "asking again reads as if the answer was lost"


def test_the_ask_requests_a_phrasing_the_reply_parser_honours(db):
    """The round trip. The ask tells the business to reply "WhatsApp"; if the
    parser did not act on that word, the ask would be collecting consent the
    system then ignored."""
    lead = _lead(db)
    assert "WhatsApp" in WHATSAPP_ASK_SENTENCE

    ri.process(lead, db, "Re: Coffee", "WhatsApp please", {}, "owner@consenttest.in")
    assert lead.consent_status == "EXPLICIT"


# ── a bug these tests uncovered in deployed production code ──────────────────

@pytest.mark.parametrize("body", ["ok", "Thanks, noted.", "Yes please.", "We are on WhatsApp."])
def test_a_human_reply_matching_no_intent_is_founder_work_not_a_crash(body):
    """analyse() indexed an empty ranking list, so any reply this table does
    not recognise raised IndexError. sync_email_replies caught that as "reply
    auto-draft failed": the reply was stored and the lead marked REPLIED, then
    the intelligence, the trust promotion and the engagement signal that closes
    an account to cold outreach were all skipped without a word.

    Most short replies land here, which is why it mattered."""
    r = ri.analyse("Re: Coffee for Consent Test Cafe", body, {})

    assert r["human"] is True
    assert r["next_action"] == "FOUNDER_REVIEW"
    assert r["needs_founder"] is True
    assert r["counts_as_engagement"] is True, "a human did reply"

def test_email_reply_with_new_number_grants_consent_from_request_context(db):
    lead = _lead(db, whatsapp_number=None, phone="+919876543210")
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_CONSENT_REQUESTED", actor="SYSTEM", channel="email", payload={"request": WHATSAPP_ASK_SENTENCE}, occurred_at=datetime.utcnow()))
    db.commit()
    result = whatsapp_consent.capture_email_reply(lead, db, "My WhatsApp number is 9876543211")
    assert result["recorded"] is True
    assert lead.whatsapp_number == "9876543211"
    assert lead.consent_phone == "9876543211"
    assert lead.consent_status == "EXPLICIT"

def test_email_reply_affirmative_uses_existing_number(db):
    lead = _lead(db, whatsapp_number=None, phone="+919876543210")
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_CONSENT_REQUESTED", actor="SYSTEM", channel="email", payload={"request": WHATSAPP_ASK_SENTENCE}, occurred_at=datetime.utcnow()))
    db.commit()
    result = whatsapp_consent.capture_email_reply(lead, db, "Yes, you can use the number you provided")
    assert result["recorded"] is True
    assert lead.consent_status == "EXPLICIT"
    assert lead.consent_phone == "+919876543210"

def test_email_reply_new_number_does_not_require_the_word_yes(db):
    lead = _lead(db, whatsapp_number=None, phone="+919876543210")
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_CONSENT_REQUESTED", actor="SYSTEM", channel="email", payload={"request": WHATSAPP_ASK_SENTENCE}, occurred_at=datetime.utcnow()))
    db.commit()
    result = whatsapp_consent.capture_email_reply(lead, db, "Use 9876543211 for WhatsApp")
    assert result["recorded"] is True
    assert lead.consent_phone == "9876543211"

def test_email_reply_without_request_context_does_not_grant_even_with_number(db):
    lead = _lead(db, whatsapp_number=None, phone="+919876543210")
    result = whatsapp_consent.capture_email_reply(lead, db, "My WhatsApp is 9876543211")
    assert result["recorded"] is False
    assert (lead.consent_status or "UNKNOWN") != "EXPLICIT"