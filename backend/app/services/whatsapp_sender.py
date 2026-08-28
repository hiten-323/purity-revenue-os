"""
WhatsApp Business API sender via AiSensy (Meta BSP) on +91 90849 58495.

WHY THE CONSENT GATE IS NOT OPTIONAL
------------------------------------
Meta's WhatsApp Business Messaging Policy requires opt-in before ANY
business-initiated message. Our leads are scraped from Google Maps / IndiaMART /
TradeIndia — consent_status is UNKNOWN for the cold pool. Sending template
messages to those numbers without opt-in is not permitted.

Cold first-touch stays on the founder's manual wa.me Send Queue. This API path
is for contacts with recorded opt-in. An inbound reply is useful evidence, but
must be represented as an explicit consent event/state before API messaging.

CONFIG (dormant until set — nothing sends without these):
  AISENSY_API_KEY        API key from the AiSensy dashboard
  AISENSY_CAMPAIGN_NAME  campaign wired to a Meta-approved template

Business-initiated messages must use an approved template; AiSensy's campaign API
maps a campaign -> template. Inside the applicable service window, provider rules
still apply.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

AISENSY_URL = "https://backend.aisensy.com/campaign/t1/api/v2"

# Only explicit/opted-in states are sufficient for API-initiated WhatsApp.
# IMPLIED_B2B is intentionally excluded: a scraped B2B relationship is not
# proof of WhatsApp opt-in.
CONSENT_OK = {"EXPLICIT", "OPTED_IN"}

# Statuses that indicate an engaged commercial relationship. They do NOT by
# themselves satisfy the WhatsApp opt-in requirement; consent must be recorded
# explicitly or an inbound WhatsApp event must be represented by the consent
# state before the API is allowed to send.
ENGAGED = {"REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT",
           "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT",
           "NEGOTIATION", "ORDER_WON", "ONBOARDED"}

SERVICE_WINDOW_HOURS = 24


@dataclass
class WaResult:
    # `sent` means AiSensy accepted the send. It does NOT mean the recipient
    # received/read it; those facts must come from provider status callbacks.
    status: str
    reason: str = ""
    message_id: str = ""
    response: str = ""
    provider_accepted: bool = False
    delivery_confirmed: bool = False


def is_configured() -> bool:
    key = (os.getenv("AISENSY_API_KEY") or "").strip()
    return bool(key and key not in ("", "your_aisensy_api_key_here"))


def consent_check(lead) -> tuple[bool, str]:
    """
    May we send this lead a WhatsApp message via the API?

    Returns (allowed, reason). Deliberately strict: an unknown consent state is
    a NO, never a maybe. IMPLIED_B2B is not accepted because it does not record
    WhatsApp opt-in.
    """
    status = (getattr(lead, "consent_status", None) or "UNKNOWN").upper()
    if getattr(lead, "do_not_call", False):
        return False, "lead is on do-not-contact"
    if status in CONSENT_OK:
        return True, f"consent recorded: {status}"
    return False, (
        f"no explicit WhatsApp opt-in on record (consent_status={status}). "
        "Business-initiated WhatsApp requires opt-in; use the founder's manual "
        "wa.me path for cold first touch."
    )


def in_service_window(lead) -> bool:
    """
    True if the lead messaged us within the last 24h — inside Meta's
    customer-service window, where free-form (non-template) text may be allowed
    by provider policy.
    """
    last = getattr(lead, "last_reply_at", None) or getattr(lead, "last_updated", None)
    if not last or (getattr(lead, "status", "") or "") not in ENGAGED:
        return False
    return (datetime.utcnow() - last) < timedelta(hours=SERVICE_WINDOW_HOURS)


def _normalise_msisdn(raw: str) -> str:
    """AiSensy wants a country-coded number without + or separators."""
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(d) == 10:
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
    Send via AiSensy. Refuses without explicit opt-in — that check comes first,
    so no future caller can bypass it by passing the right arguments.

    A successful HTTP response means PROVIDER_ACCEPTED only. Delivery/read
    status must be established separately from provider callbacks.
    """
    allowed, reason = consent_check(lead)
    if not allowed:
        return WaResult(status="blocked", reason=reason)

    if not is_configured():
        return WaResult(status="not_configured",
                        reason="AISENSY_API_KEY not set — add it to enable API sending")

    phone = _normalise_msisdn(getattr(lead, "whatsapp_number", None) or getattr(lead, "phone", "") or "")
    if len(phone) < 11:
        return WaResult(status="blocked", reason="no usable WhatsApp number on the lead")

    campaign = (campaign_name or os.getenv("AISENSY_CAMPAIGN_NAME") or "").strip()
    if not campaign:
        return WaResult(status="not_configured",
                        reason="AISENSY_CAMPAIGN_NAME not set — it must map to a Meta-approved template")

    payload = {
        "apiKey": (os.getenv("AISENSY_API_KEY") or "").strip(),
        "campaignName": campaign,
        "destination": phone,
        "userName": getattr(lead, "contact_name", None) or getattr(lead, "company", "") or "there",
        "source": "purity-revenue-os",
        "templateParams": template_params if template_params is not None else [
            (getattr(lead, "contact_name", None) or getattr(lead, "company", "") or "there")
        ],
    }
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(AISENSY_URL, json=payload)
        body = (r.text or "")[:1000]
        if r.status_code // 100 == 2:
            message_id = _extract_provider_message_id(body, r.headers)
            return WaResult(
                status="sent",
                reason="AiSensy accepted the send request; delivery must be confirmed by webhook/status callback",
                message_id=message_id,
                response=body[:300],
                provider_accepted=True,
                delivery_confirmed=False,
            )
        return WaResult(status="failed", reason=f"AiSensy HTTP {r.status_code}", response=body[:300])
    except Exception as e:
        return WaResult(status="failed", reason=f"{type(e).__name__}: {str(e)[:120]}")
