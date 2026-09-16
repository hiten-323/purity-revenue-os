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

CONSENT_OK = {"EXPLICIT", "OPTED_IN"}

ENGAGED = {"REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT",
           "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT",
           "NEGOTIATION", "ORDER_WON", "ONBOARDED"}

SERVICE_WINDOW_HOURS = 24


@dataclass
class WaResult:
    status: str
    reason: str = ""
    message_id: str = ""
    response: str = ""
    provider_accepted: bool = False
    delivery_confirmed: bool = False


def is_configured() -> bool:
    from app.services import whatsapp_evolution as transport
    return transport.config_status()[0]


def consent_check(lead) -> tuple[bool, str]:
    """May we send this lead a WhatsApp message via the API?"""
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
    last = getattr(lead, "last_reply_at", None) or getattr(lead, "last_updated", None)
    if not last or (getattr(lead, "status", "") or "") not in ENGAGED:
        return False
    return (datetime.utcnow() - last) < timedelta(hours=SERVICE_WINDOW_HOURS)


def _normalise_msisdn(raw: str) -> str:
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(d) == 10:
        d = "91" + d
    return d


def _extract_provider_message_id(response_text: str, headers) -> str:
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
    """The single WhatsApp send path. Consent first, then the one transport."""
    allowed, reason = consent_check(lead)
    if not allowed:
        return WaResult(status="blocked", reason=reason)

    from app.services import whatsapp_evolution as transport

    phone = getattr(lead, "whatsapp_number", None) or getattr(lead, "phone", "") or ""
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