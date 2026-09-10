"""
WhatsApp Business API sender via AiSensy (Meta BSP) on +91 90849 58495.

WHY THE CONSENT GATE IS NOT OPTIONAL
------------------------------------
Meta's WhatsApp Business Messaging Policy requires opt-in before ANY
business-initiated message. Our leads are scraped from Google Maps / IndiaMART /
TradeIndia — consent_status is UNKNOWN for 736 of 737. Sending template messages
to those numbers is a policy violation, and the practical consequence is not a
fine: recipients block/report -> quality rating drops -> messaging limits ->
the number gets banned. That is the same number our email + outreach copy tells
prospects to call, so losing it costs more than the channel.

So this module refuses to send to a lead without recorded consent. Cold
first-touch stays on wa.me (the founder's own phone, manual send) via the
WhatsApp Send Queue. This API path is for people who have opted in — in practice
someone who REPLIED, which both proves consent and opens Meta's 24-hour
customer-service window where free-form (non-template) messages are allowed.

CONFIG (dormant until set — nothing sends without these):
  AISENSY_API_KEY        API key from the AiSensy dashboard
  AISENSY_CAMPAIGN_NAME  campaign wired to a Meta-approved template

Business-initiated messages must use an approved template; AiSensy's campaign API
maps a campaign -> template. Inside the 24h window free-form text is allowed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

# The endpoint constant is gone: whatsapp_evolution owns where a message
# goes, and a constant here is what let a second transport appear.

# Consent values we treat as a real opt-in.
CONSENT_OK = {"EXPLICIT", "IMPLIED_B2B", "OPTED_IN"}

# Statuses that prove the lead messaged/replied to us — that is an opt-in and
# opens Meta's 24h customer-service window.
ENGAGED = {"REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT",
           "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT",
           "NEGOTIATION", "ORDER_WON", "ONBOARDED"}

SERVICE_WINDOW_HOURS = 24


@dataclass
class WaResult:
    # `sent` means AiSensy accepted the request. It does NOT mean the recipient
    # received/read it; those facts must come from provider status callbacks.
    status: str                      # sent | blocked | failed | not_configured
    reason: str = ""
    message_id: str = ""
    response: str = ""
    provider_accepted: bool = False
    delivery_confirmed: bool = False


def is_configured() -> bool:
    # Asks the one transport rather than looking for a provider key of its
    # own — two answers to "can we send" is how three transports happened.
    from app.services import whatsapp_evolution as transport
    return transport.config_status()[0]


def consent_check(lead) -> tuple[bool, str]:
    """
    May we send this lead a WhatsApp message via the API?

    Returns (allowed, reason). Deliberately strict: an unknown consent state is
    a NO, never a maybe.
    """
    status = (getattr(lead, "consent_status", None) or "UNKNOWN").upper()
    if getattr(lead, "do_not_call", False):
        return False, "lead is on do-not-contact"
    if status in CONSENT_OK:
        return True, f"consent recorded: {status}"
    if (getattr(lead, "status", "") or "") in ENGAGED:
        return True, "lead replied to us — opt-in + 24h service window open"
    return False, (
        f"no opt-in on record (consent_status={status}). Meta requires opt-in before "
        f"business-initiated WhatsApp. Use the wa.me Send Queue for cold first touch."
    )


def in_service_window(lead) -> bool:
    """
    True if the lead messaged us within the last 24h — inside Meta's
    customer-service window, where free-form (non-template) text is allowed.
    Outside it, only an approved template may be sent.
    """
    last = getattr(lead, "last_reply_at", None) or getattr(lead, "last_updated", None)
    if not last or (getattr(lead, "status", "") or "") not in ENGAGED:
        return False
    return (datetime.utcnow() - last) < timedelta(hours=SERVICE_WINDOW_HOURS)


def _normalise_msisdn(raw: str) -> str:
    """AiSensy wants a country-coded number without + or separators."""
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(d) == 10:            # bare Indian mobile
        d = "91" + d
    return d


def _extract_provider_message_id(response_text: str, headers) -> str:
    """Extract a provider message identifier without assuming one transport shape."""
    header_id = str(headers.get("x-message-id") or headers.get("x-messageid") or "").strip()
    if header_id:
        return header_id

    try:
        import json
        body = json.loads(response_text or "{}")
    except Exception:
        return ""

    if not isinstance(body, dict):
        return ""
    for key in ("messageId", "message_id", "id", "data"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested in ("messageId", "message_id", "id"):
                nested_value = value.get(nested)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
    return ""


def send_whatsapp(lead, message: str, campaign_name: Optional[str] = None,
                  template_params: Optional[list[str]] = None,
                  timeout: float = 20.0) -> WaResult:
    """
    The single WhatsApp send path. Consent first, then the one transport.

    Consent is checked here and nowhere below, on purpose, so no future caller
    can bypass it by passing the right arguments — and so the transport cannot
    grow a second opinion about who may be messaged.

    The transport is now Evolution API driving Meta's WhatsApp Cloud API.
    Previously this POSTed to AiSensy directly, as did whatsapp_connector and
    whatsapp_gateway/client: three modules each defining AISENSY_URL, each
    transmitting, and one of them (the connector) with no consent check at all
    until it was fixed mid-audit. One transport removes the shape of that bug,
    not just this instance of it.

    A successful response means PROVIDER_ACCEPTED only. Delivery and read must
    be established from webhooks, never inferred from the send call.
    """
    allowed, reason = consent_check(lead)
    if not allowed:
        return WaResult(status="blocked", reason=reason)

    from app.services import whatsapp_evolution as transport

    phone = getattr(lead, "whatsapp_number", None) or getattr(lead, "phone", "") or ""
    # AISENSY_CAMPAIGN_NAME named an AiSensy campaign that mapped to a
    # Meta-approved template. WHATSAPP_TEMPLATE names that template directly;
    # the old variable is still read so an existing install keeps working.
    template = (campaign_name
                or os.getenv("WHATSAPP_TEMPLATE")
                or os.getenv("AISENSY_CAMPAIGN_NAME") or "").strip()
    params = template_params if template_params is not None else [
        (getattr(lead, "contact_name", None) or getattr(lead, "company", "") or "there")
    ]

    result = transport.send_template(phone, template, params=params,
                                     timeout=timeout)
    return WaResult(
        status=result.status,
        reason=result.reason,
        message_id=result.message_id,
        response=result.response,
        provider_accepted=result.provider_accepted,
        delivery_confirmed=result.delivery_confirmed,
    )
