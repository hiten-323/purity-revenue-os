"""Governed WhatsApp-consent acquisition helpers.

A WhatsApp number discovered during enrichment is never consent. Consent may be
acquired through another permitted channel when the person is clearly asked
whether they want WhatsApp communication from Pure Pantry Provisions.

Email flow:
  outbound email explicitly asks for a WhatsApp number
  -> record WHATSAPP_CONSENT_REQUESTED
  -> inbound reply supplies one mobile number
  -> bind EXPLICIT consent to that number and preserve the evidence event.

The helper deliberately refuses ambiguous replies with multiple numbers.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.models.models import WorkflowEvent

_MOBILE = re.compile(r"(?<!\d)(?:(?:\+91|0091)[\s-]?)?([6-9]\d{9})(?!\d)")
_REQUEST_MAX_DAYS = 30


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")[-10:]


def extract_mobile_numbers(text: str) -> list[str]:
    """Return unique Indian mobile numbers explicitly present in text."""
    found = []
    for match in _MOBILE.finditer(text or ""):
        number = match.group(1)
        if number not in found:
            found.append(number)
    return found


def record_email_whatsapp_request(lead, db, *, message_id: str = "") -> None:
    """Record that an outbound email explicitly asked for WhatsApp contact."""
    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="WHATSAPP_CONSENT_REQUESTED",
        actor="SYSTEM",
        channel="email",
        payload={
            "source": "EMAIL_WHATSAPP_REQUEST",
            "business": "Pure Pantry Provisions",
            "message_id": message_id,
        },
        occurred_at=datetime.utcnow(),
    ))


def capture_email_reply(lead, db, body: str, *, message_id: str = "") -> dict:
    """Capture WhatsApp opt-in from a reply to a recorded consent request.

    A reply is eligible only when:
      1. Revenue OS previously sent this lead an email explicitly asking for a
         WhatsApp number; and
      2. the reply contains exactly one Indian mobile number.

    The second condition makes the shared number the consented destination.
    A bare number without a preceding request is never treated as consent.
    """
    events = (db.query(WorkflowEvent)
              .filter(
                  WorkflowEvent.lead_id == lead.id,
                  WorkflowEvent.event_type == "WHATSAPP_CONSENT_REQUESTED",
                  WorkflowEvent.channel == "email",
              )
              .order_by(WorkflowEvent.occurred_at.desc())
              .all())
    now = datetime.utcnow()
    recent = next(
        (e for e in events
         if not e.occurred_at or now - e.occurred_at <= timedelta(days=_REQUEST_MAX_DAYS)),
        None,
    )
    if recent is None:
        return {"captured": False, "reason": "no recent WhatsApp consent request"}

    numbers = extract_mobile_numbers(body)
    if len(numbers) == 0:
        return {"captured": False, "reason": "reply contains no mobile number"}
    if len(numbers) > 1:
        return {"captured": False, "reason": "reply contains multiple mobile numbers"}

    wa_number = numbers[0]
    lead.whatsapp_number = wa_number
    lead.consent_status = "EXPLICIT"
    lead.consent_source = "EMAIL_WHATSAPP_REQUEST"
    lead.consent_timestamp = now
    lead.consent_phone = wa_number

    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="CONSENT_GIVEN",
        actor="PROSPECT",
        channel="email",
        payload={
            "consent_status": "EXPLICIT",
            "source": "EMAIL_WHATSAPP_REQUEST",
            "consent_phone": wa_number,
            "message_id": message_id,
            "request_event_id": recent.id,
            "evidence": "prospect supplied a WhatsApp number in response to the WhatsApp request",
        },
        occurred_at=now,
    ))
    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="NEXT_ACTION_SET",
        actor="SYSTEM",
        channel="email",
        payload={
            "action": "SEND_WHATSAPP",
            "detail": "prospect supplied WhatsApp number after explicit WhatsApp request",
            "from_outcome": "EMAIL_WHATSAPP_CONSENT",
            "blocked": None,
        },
        occurred_at=now,
    ))
    return {
        "captured": True,
        "whatsapp_number": wa_number,
        "source": "EMAIL_WHATSAPP_REQUEST",
    }
