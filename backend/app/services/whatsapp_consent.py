"""
The one place WhatsApp consent is written.

Consent was being created in three files, each with its own idea of what to
record: founder_call_pipeline on a WHATSAPP_OPT_IN call outcome,
phone_intelligence on a founder call, and the inbound webhook when a business
messages the business number first. Three writers for one permission is how
this codebase came to have addresses whose consent nobody can now account
for.

WHAT COUNTS, AND WHAT DELIBERATELY DOES NOT
-------------------------------------------
Consent is a sentence somebody said, not a fact we discovered. None of these
are consent, however convenient they would be:

    finding a mobile number during enrichment
    a business publishing a WhatsApp number on its website
    someone answering a phone call
    replying to an email without agreeing to WhatsApp
    a number already sitting in the CRM
    whatsapp_verified being True — that proves an account exists, and
      "an account exists" is not "you may message it"

So record() takes a SOURCE from a fixed vocabulary and EVIDENCE — the words
the business actually used. A caller that cannot supply both is a caller
inventing permission.

BOUND TO A NUMBER, NOT A ROW
----------------------------
consent_status lives on the lead, but permission was given for one
destination. consent_phone captures which, and whatsapp_sender.consent_check
refuses when the row's number no longer matches. An opt-in given on one
number does not transfer to whatever number lands on the record later.
"""
from __future__ import annotations

from datetime import datetime

# Recognised provenances. Each is a real conversation someone can be shown.
SOURCES = {
    "AI_CALL_WHATSAPP_REQUEST":
        "asked for WhatsApp during the disclosed AI qualification call",
    "EMAIL_REPLY_WHATSAPP_REQUEST":
        "replied to an email asking whether WhatsApp was easier",
    "FOUNDER_CALL":
        "told the founder on a call",
    "WHATSAPP_INBOUND":
        "messaged the business number first, which is opt-in under Meta's rules",
}

CONSENT_GRANTED = "EXPLICIT"


def destination(lead, supplied_number: str = "") -> str:
    """The number consent would cover — the same resolution order
    whatsapp_sender.send_whatsapp uses to pick where a message goes. If the
    two disagreed, consent would be recorded against one number and the
    message sent to another."""
    return (
        (supplied_number or getattr(lead, "whatsapp_number", None) or getattr(lead, "phone", "") or "")
    ).strip()


def record(lead, db, *, source: str, evidence: str, message_id: str = "", create_next_action: bool = True) -> dict:
    """Record WhatsApp consent, or explain why it was not recorded.

    Returns a dict rather than raising on the ordinary refusals, because the
    callers are reply handlers and call-outcome handlers that must keep
    processing the rest of their batch.
    """
    from app.models.models import WorkflowEvent

    if source not in SOURCES:
        raise ValueError(
            f"{source!r} is not a recognised consent source; expected one of "
            f"{sorted(SOURCES)}. A source that is not in this list is a caller "
            f"inventing a provenance."
        )

    evidence = (evidence or "").strip()
    if not evidence:
        raise ValueError(
            "consent requires evidence — the words the business actually used. "
            "Recording permission with nothing to show is how consent nobody "
            "can account for gets into a database."
        )

    number = destination(lead)
    now = datetime.utcnow()

    # An opt-in with no number to bind it to. Recorded as a fact so a human can
    # chase the number, but NOT as consent: a blank consent_phone would let the
    # opt-in attach to whatever number is discovered next, which is exactly the
    # inference this module exists to prevent.
    if not number:
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="WHATSAPP_CONSENT_UNBOUND",
            actor="SYSTEM", channel="whatsapp",
            payload={"source": source, "basis": SOURCES[source],
                     "evidence": evidence[:1000], "message_id": message_id,
                     "note": "opt-in given but no WhatsApp number on record — "
                             "consent NOT granted until a number exists"},
            occurred_at=now))
        return {"recorded": False, "reason": "no WhatsApp number on record to bind consent to"}

    from app.services.identity import digits_only

    already = (getattr(lead, "consent_status", "") or "").upper() == CONSENT_GRANTED
    same_number = digits_only(getattr(lead, "consent_phone", "") or "") == digits_only(number)
    if already and same_number:
        return {"recorded": False, "reason": "consent already on record for this number",
                "consent_phone": number}

    lead.consent_status = CONSENT_GRANTED
    lead.consent_source = source
    lead.consent_timestamp = now
    lead.consent_phone = number

    # The evidence row. Immutable, and the thing to produce years later when
    # someone asks who said we could.
    db.add(WorkflowEvent(
        lead_id=lead.id, event_type="WHATSAPP_CONSENT_RECORDED",
        actor="SYSTEM", channel="whatsapp",
        payload={"source": source, "basis": SOURCES[source],
                 "evidence": evidence[:1000], "message_id": message_id,
                 "consent_phone": number},
        occurred_at=now))

    # Call-outcome logging already creates the canonical next-action event.
    if create_next_action:
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="NEXT_ACTION_SET", actor="SYSTEM",
            channel="whatsapp",
            payload={"action": "SEND_WHATSAPP",
                     "detail": f"WhatsApp consent recorded — {SOURCES[source]}",
                     "from_outcome": source, "blocked": None},
            occurred_at=now))

    return {"recorded": True, "source": source, "consent_phone": number}


def capture_email_reply(lead, db, body: str, *, message_id: str = "") -> dict:
    """Capture consent from a reply to the dedicated WhatsApp-number request.

    A number supplied in that reply is affirmative even without the word yes.
    A clear affirmative reply without a number may use the single existing
    contact number. No prior request means no inferred consent.
    """
    import re
    from app.models.models import WorkflowEvent

    request = (db.query(WorkflowEvent)
               .filter(WorkflowEvent.lead_id == lead.id,
                       WorkflowEvent.event_type == "WHATSAPP_CONSENT_REQUESTED",
                       WorkflowEvent.channel == "email")
               .order_by(WorkflowEvent.occurred_at.desc()).first())
    if not request:
        return {"recorded": False, "reason": "no WhatsApp consent request"}

    numbers = list(dict.fromkeys(re.findall(r"(?<!\\d)(?:(?:\\+91|0091)[\\s-]?)?([6-9]\\d{9})(?!\\d)", body or "")))
    if len(numbers) > 1:
        return {"recorded": False, "reason": "multiple WhatsApp numbers are ambiguous"}

    supplied = numbers[0] if numbers else ""
    text = (body or "").lower()
    affirmative = any(term in text for term in (
        "yes", "sure", "okay", "ok", "please", "use my number",
        "use this number", "whatsapp me", "you can whatsapp",
    ))
    existing = (getattr(lead, "phone", None) or "").strip()
    if not supplied and not affirmative:
        return {"recorded": False, "reason": "no number or affirmative confirmation"}
    target = supplied or existing
    if not target:
        return {"recorded": False, "reason": "no WhatsApp number to bind"}
    if supplied:
        lead.whatsapp_number = supplied
        lead.consent_phone = supplied
    result = record(lead, db, source="EMAIL_REPLY_WHATSAPP_REQUEST",
                    evidence=(body or "")[:1000], message_id=message_id)
    if result.get("recorded") and not supplied:
        lead.consent_phone = existing
    return result