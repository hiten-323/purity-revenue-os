r"""
The ONE WhatsApp transport: AiSensy's API-campaign endpoint.

Why AiSensy and not Evolution/Cloud API
---------------------------------------
Evolution driving Meta's Cloud API was the stated target, and this module
replaces it. The reason is not preference, it is a hard platform constraint:

    A phone number belongs to exactly one WhatsApp Business Account.

That number is already live under AiSensy, with a High quality rating, an
approved template set and seven Live campaigns carrying real commerce --
Order Confirmed, Order Fulfilled, Order Cancelled, COD Confirmation, Reorder,
Order Feedback, Abandoned Cart. Standing up Evolution against the same number
would mean migrating it away from AiSensy and breaking all of that. Building
the pipe we want would have broken the pipe that pays.

So the transport moved to where the number already is. The chokepoint did not
move: whatsapp_sender.send_whatsapp is still the only way out, consent is
still checked there and nowhere else, and this module still holds no opinion
about who may be messaged.

The contract, taken from AiSensy's API reference, not from memory
-----------------------------------------------------------------
    POST https://backend.aisensy.com/campaign/t1/api/v2

    apiKey          required
    campaignName    required -- and the campaign's status must be LIVE
    destination     required -- country dial code; "+" optional for India
    userName        required
    templateParams  optional, BUT its length must equal the number of
                    variables in the campaign's template or the request is
                    rejected outright
    source, tags, attributes, media   optional

A campaign, not a template
--------------------------
This is the shape difference from every other transport in this codebase.
Evolution named a template directly; AiSensy names a CAMPAIGN, which is a
saved binding of one approved template plus its settings. So WHATSAPP_TEMPLATE
here is a campaign name, and pointing it at a campaign that is not Live is the
most likely reason a correct-looking send does nothing.

Never point prospecting at a transactional campaign
---------------------------------------------------
The Live campaigns listed above are UTILITY templates addressed to people who
placed an order. Sending cold B2B prospecting through one is a Meta policy
violation, and the cost is not abstract: the quality rating those order flows
depend on is the same rating a prospecting complaint damages. Prospecting gets
its own MARKETING campaign, or it does not go.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

# One definition of a dialable destination, shared with every other sender.
from app.services.identity import msisdn as normalise_msisdn

API_URL = "https://backend.aisensy.com/campaign/t1/api/v2"
SOURCE = "purity-revenue-os"


@dataclass(frozen=True)
class SendResult:
    """A provider ACCEPTING a message is not the same as it being delivered."""
    status: str                  # sent | blocked | not_configured | failed
    reason: str = ""
    message_id: str = ""
    response: str = ""
    provider_accepted: bool = False
    delivery_confirmed: bool = False
    http_status: int = 0


def enabled() -> bool:
    """Off unless explicitly switched on. Never defaults to sending."""
    return (os.getenv("AISENSY_ENABLED", "0") or "0").strip().lower() in (
        "1", "true", "yes", "on")


def api_key() -> str:
    return (os.getenv("AISENSY_API_KEY", "") or "").strip()


def campaign() -> str:
    """The Live API campaign prospecting sends through.

    WHATSAPP_TEMPLATE is read first because that is what the rest of the
    system calls this concept; AISENSY_CAMPAIGN_NAME is the older name and is
    still honoured so an existing install keeps working.
    """
    return ((os.getenv("WHATSAPP_TEMPLATE", "")
             or os.getenv("AISENSY_CAMPAIGN_NAME", "")) or "").strip()


def config_status() -> tuple[bool, str]:
    """What is missing, named precisely, so the answer is actionable."""
    if not enabled():
        return False, "AISENSY_ENABLED is not 1 — WhatsApp sending is off"

    key = api_key()
    if not key or key in ("", "your_aisensy_api_key_here"):
        return False, ("AISENSY_API_KEY is not set. It is the API Campaign Key "
                       "from AiSensy → Developer, not a Project API Key.")
    if not key.isascii():
        # The same trap the LiveKit secret fell into twice: a masked dashboard
        # value (32 bullet characters) copied instead of the secret behind it.
        # HTTP headers and JSON bodies both choke on it in ways that name the
        # codec rather than the mistake, so name the mistake here.
        return False, ("AISENSY_API_KEY contains non-ASCII characters. The "
                       "usual cause is copying a masked value (••••) from the "
                       "dashboard rather than the key behind it.")
    if not campaign():
        return False, ("No campaign named. Set WHATSAPP_TEMPLATE to the name "
                       "of a LIVE AiSensy API campaign. A campaign that is "
                       "not Live accepts nothing.")
    live = (os.getenv("WHATSAPP_CAMPAIGN_LIVE", "0") or "0").strip().lower()
    if live not in ("1", "true", "yes", "on"):
        return False, ("WhatsApp campaign live-state is not explicitly verified. "
                       "Set WHATSAPP_CAMPAIGN_LIVE=1 only after confirming the "
                       "configured AiSensy campaign is LIVE and approved.")
    return True, f"AiSensy campaign={campaign()!r} (live verified)"


def extract_message_id(response_text: str, headers: Any = None) -> str:
    """Pull a provider message id out of whatever shape came back.

    AiSensy's documented success contract is only "status 200" -- no id is
    promised. So this looks in the header first and then across the body
    shapes seen in practice, and callers must treat an empty result as
    "unreconcilable", never as "failed". Reporting a real send as a failure is
    a bug this codebase has already shipped once.
    """
    try:
        header_id = ""
        if headers is not None:
            header_id = str(headers.get("x-message-id")
                            or headers.get("x-messageid") or "").strip()
        if header_id:
            return header_id
    except Exception:  # noqa: BLE001 - a header lookup must not break a send
        pass

    try:
        body = json.loads(response_text or "{}")
    except (ValueError, TypeError):
        return ""
    if not isinstance(body, dict):
        return ""

    for key in ("messageId", "message_id", "id", "wamid", "data", "messages"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            value = [value]
        if isinstance(value, list):
            # Meta and several BSPs answer {"messages":[{"id":"wamid...."}]}.
            for row in value:
                if not isinstance(row, dict):
                    continue
                for nested in ("messageId", "message_id", "id", "wamid"):
                    nested_value = row.get(nested)
                    if isinstance(nested_value, str) and nested_value.strip():
                        return nested_value.strip()
    return ""


def send_template(phone: str, template: str, *, params: list[str] | None = None,
                  language: str = "en", timeout: float = 20.0,
                  user_name: str = "", client: Any = None) -> SendResult:
    """Send through ONE Live API campaign. The only way out of this system.

    Consent is not checked here and must not be: whatsapp_sender.consent_check
    owns that and runs before this is called. A transport that re-checks
    consent becomes a second authority, and two authorities on one question
    eventually disagree -- which is how a WhatsApp path once shipped with no
    consent check at all.

    `template` names an AiSensy CAMPAIGN. `language` is accepted so this
    matches the transport signature every caller already uses, and ignored
    because the campaign's own template fixes the language.

    `client` exists so the gateway and the tests can drive this without a
    network. It is an injection point, not a second transport: there is one
    request built in one place, and everything that sends goes through it.
    """
    ok, detail = config_status()
    if not ok:
        return SendResult(status="not_configured", reason=detail)

    number = normalise_msisdn(phone)
    if len(number) < 11:
        return SendResult(status="blocked",
                          reason=f"no usable WhatsApp number: {phone!r}")

    name = (template or "").strip() or campaign()
    if not name:
        return SendResult(
            status="blocked",
            reason=("no campaign named. Meta permits a business-initiated "
                    "conversation only through an approved template; free "
                    "text is refused here rather than attempted and rejected."))

    payload: dict[str, Any] = {
        "apiKey": api_key(),
        "campaignName": name,
        "destination": number,
        "userName": (user_name or "there").strip()[:100],
        "source": SOURCE,
    }
    if params:
        # Length must match the campaign's template exactly or AiSensy rejects
        # the whole request. We cannot count the template's variables from
        # here, so a mismatch surfaces as their 4xx with their own wording.
        payload["templateParams"] = [str(p) for p in params]

    headers = {"Content-Type": "application/json"}
    try:
        if client is not None:
            r = client.post(API_URL, json=payload, headers=headers)
        else:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(API_URL, json=payload, headers=headers)

        body = (getattr(r, "text", None) or "")[:1000]
        code = int(getattr(r, "status_code", 0) or 0)

        if not 200 <= code < 300:
            return SendResult(status="failed",
                              reason=f"AiSensy HTTP {code}",
                              response=body[:300], http_status=code)

        message_id = extract_message_id(body, getattr(r, "headers", {}) or {})
        if message_id:
            return SendResult(
                status="sent", message_id=message_id, response=body[:300],
                provider_accepted=True, delivery_confirmed=False,
                http_status=code,
                reason=("AiSensy accepted the send; delivery and read must be "
                        "confirmed by webhook, not assumed from this response"))

        # 2xx with no id. Unlike Evolution, AiSensy does not promise one, so
        # this is an accepted send that cannot later be reconciled -- not a
        # failure. Say exactly that rather than picking the convenient reading.
        return SendResult(
            status="sent", message_id="", response=body[:300],
            provider_accepted=True, delivery_confirmed=False, http_status=code,
            reason=("AiSensy returned 2xx with no message id — the send was "
                    "accepted but cannot be matched to a delivery webhook"))
    except Exception as exc:  # noqa: BLE001 — unreachable is a status, not a crash
        return SendResult(status="failed",
                          reason=f"{type(exc).__name__}: {str(exc)[:120]}")


def health() -> dict[str, Any]:
    """Configuration state, reported verbatim.

    There is no reachability probe. AiSensy exposes no unauthenticated health
    endpoint, and the only other call available is a real send -- which is
    precisely what a health check must not do.
    """
    ok, detail = config_status()
    return {
        "configured": ok,
        "detail": detail,
        "transport": "aisensy",
        "url": API_URL,
        "campaign": campaign(),
        "enabled": enabled(),
        "can_verify_numbers": False,
    }
