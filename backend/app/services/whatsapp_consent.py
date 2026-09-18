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

import re
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


# ── Consent from a reply to the dedicated WhatsApp request ──────────────────
#
# The first version of this function granted consent to EVERY one of these,
# verified against the code before it was replaced:
#
#     "Please remove me from your list."        "please" matched as a yes
#     "Not interested, please don't contact"    same
#     "No thanks. Sent from Outlook for ..."    "ok" inside "Outlook"
#     "I'm not sure this is relevant"           "sure" inside "not sure"
#     "We will look into it later."             "ok" inside "look"
#     "No thanks. --  Rahul  9876543210"        the SIGNATURE number, which it
#                                               also wrote over whatsapp_number
#     an out-of-office auto-reply               no human check at all
#
# and bound the rest to lead.phone, which for most leads is a published
# landline WhatsApp cannot reach. An opt-out becoming a WhatsApp opt-in is the
# single worst outcome this module can produce.
#
# So the rules, in order, each failing toward "not recorded":
#   1. We must have asked, by email (WHATSAPP_CONSENT_REQUESTED).
#   2. Only the business's NEW words count. Quoted history and signature
#      blocks are cut first: a signature number was never offered, and a
#      quoted email is our text, not theirs.
#   3. A machine reply is never a business agreeing to anything.
#   4. Any refusal, opt-out or negation refuses. A missed opt-in costs a
#      founder a follow-up; a false one messages someone who said no.
#   5. A yes is a word, matched on word boundaries: "okay" is a yes, "Outlook"
#      is not, and "please" on its own is not a yes at all.
#   6. Exactly one number, from their new words, or an explicit yes to using
#      the number already on file -- and never a landline.

_QUOTE_OR_SIGNATURE_START = re.compile(
    r"^\s*(?:"
    r"on\b.{0,120}\bwrote:\s*$"                    # Gmail / Apple Mail
    r"|-{2,}\s*original message\s*-{2,}"           # Outlook
    r"|from:\s.+"                                  # forwarded / Outlook header block
    r"|--\s*$"                                     # RFC 3676 signature delimiter
    r"|sent from (?:my\s+)?\w+"                    # mobile client footers
    r"|get outlook for\b"
    r")",
    re.IGNORECASE,
)

_NEGATION = re.compile(
    r"\b(?:no|not|nope|don'?t|do not|never|stop|remove|unsubscribe|"
    r"no thanks|not interested)\b",
    re.IGNORECASE,
)

_AFFIRMATIVE = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|please do|"
    r"use (?:this|my|that|the) number|you (?:can|may) (?:use|whatsapp)|whatsapp me)\b",
    re.IGNORECASE,
)

_INDIAN_MOBILE = re.compile(r"(?<!\d)(?:(?:\+91|0091|91)[\s-]?)?([6-9]\d{4}[\s-]?\d{5})(?!\d)")

_REFUSING_INTENTS = {"DO_NOT_CONTACT", "NOT_INTERESTED", "NO_REQUIREMENT"}


def new_text(body: str) -> str:
    """What the business actually wrote in this reply: everything before the
    first quoted-history or signature marker, minus any '>' quoted lines."""
    kept = []
    for line in (body or "").splitlines():
        if _QUOTE_OR_SIGNATURE_START.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def capture_email_reply(lead, db, body: str, *, message_id: str = "",
                        subject: str = "", headers: dict | None = None) -> dict:
    """Consent from a reply to the dedicated WhatsApp request, or the reason
    there is none. Every refusal returns rather than raises; the caller is a
    reply loop that must keep processing its batch."""
    from app.models.models import WorkflowEvent
    from app.services import identity
    from app.services import reply_intelligence as ri

    asked = (db.query(WorkflowEvent)
             .filter(WorkflowEvent.lead_id == lead.id,
                     WorkflowEvent.event_type == "WHATSAPP_CONSENT_REQUESTED",
                     WorkflowEvent.channel == "email")
             .order_by(WorkflowEvent.occurred_at.desc()).first())
    if not asked:
        return {"recorded": False, "reason": "no WhatsApp consent request"}

    text = new_text(body)
    if not text:
        return {"recorded": False, "reason": "no new text from the business"}

    sender = ri.classify_sender(subject or "", text, headers or {})
    if sender.get("sender") != ri.HUMAN:
        return {"recorded": False, "reason": f"machine reply ({sender.get('kind')})"}

    refusing = {i["intent"] for i in ri.classify_intent(text)["intents"]} & _REFUSING_INTENTS
    if refusing:
        return {"recorded": False, "reason": f"reply refuses ({', '.join(sorted(refusing))})"}
    if _NEGATION.search(text):
        return {"recorded": False, "reason": "reply contains a negation — founder reads it"}

    numbers = list(dict.fromkeys(re.sub(r"[\s-]", "", m) for m in _INDIAN_MOBILE.findall(text)))
    if len(numbers) > 1:
        return {"recorded": False, "reason": "multiple numbers in the reply are ambiguous"}
    supplied = numbers[0] if numbers else ""

    if not supplied and not _AFFIRMATIVE.search(text):
        return {"recorded": False, "reason": "no number and no explicit yes"}

    target = supplied or destination(lead)
    if not target:
        return {"recorded": False, "reason": "no WhatsApp number to bind"}
    if identity.is_landline(target):
        return {"recorded": False,
                "reason": f"{target} is a landline — WhatsApp cannot reach it"}

    if supplied:
        # The business gave us this number in reply to a request for exactly
        # that, so it is the one destination consent covers.
        lead.whatsapp_number = supplied

    return record(lead, db, source="EMAIL_REPLY_WHATSAPP_REQUEST",
                  evidence=text[:1000], message_id=message_id)
