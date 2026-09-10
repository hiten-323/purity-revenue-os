r"""
The one and only WhatsApp transport: Evolution API in Meta Cloud API mode.

Why one
-------
There were three. whatsapp_sender, whatsapp_connector and
whatsapp_gateway/client each defined AISENSY_URL and each POSTed to it, so
"can we message this business" had three answers that only happened to agree.
One of them (the connector) reached SMTP-equivalent transmission with no
consent check at all until it was fixed mid-audit. That is the same defect
class as the tender pricer's private SMTP socket, and it recurs because a
transport is easy to add and nothing forces a new one through the gate.

This module is the only place a WhatsApp message leaves the system. Everything
else calls whatsapp_sender.send_whatsapp, which checks consent and then calls
here.

Cloud API, not Baileys
----------------------
Evolution can drive either the official Meta WhatsApp Cloud API or an
unofficial WhatsApp-Web/Baileys session. Only the first is used here, and the
choice is enforced rather than documented: integration="WHATSAPP-BAILEYS" is
refused outright.

Baileys drives a real WhatsApp account over an unofficial protocol. For a
brand's main outreach that risks the number being banned, gives no template
approval, and produces no dependable delivery receipts. A ban would take the
business's actual WhatsApp presence with it, not just this integration.

Template-gated by design
------------------------
Meta only permits business-initiated conversations through an approved
template. That is not a limitation to work around — it is the mechanism that
makes B2B WhatsApp outreach legitimate, and it is why 1,273 businesses that
have a number are currently unreachable: they have a number, and no opt-in.
A free-text first message is refused here, not attempted and failed at Meta.

Off unless configured
---------------------
EVOLUTION_ENABLED must be 1 and every credential present. An unconfigured
install returns not_configured and sends nothing.

    EVOLUTION_ENABLED=1
    EVOLUTION_URL=http://127.0.0.1:8080
    EVOLUTION_API_KEY=...
    EVOLUTION_INSTANCE=...          the instance name in Evolution
    EVOLUTION_INTEGRATION=WHATSAPP-BUSINESS      (Cloud API; the default)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

CLOUD_API = "WHATSAPP-BUSINESS"
BAILEYS = "WHATSAPP-BAILEYS"

DEFAULT_URL = "http://127.0.0.1:8080"


@dataclass(frozen=True)
class SendResult:
    """A provider ACCEPTING a message is not the same as it being delivered."""
    status: str                  # sent | blocked | not_configured | failed
    reason: str = ""
    message_id: str = ""
    response: str = ""
    provider_accepted: bool = False
    delivery_confirmed: bool = False


def enabled() -> bool:
    return (os.getenv("EVOLUTION_ENABLED", "0") or "0").strip().lower() in (
        "1", "true", "yes", "on")


def base_url() -> str:
    return (os.getenv("EVOLUTION_URL", "") or DEFAULT_URL).strip().rstrip("/")


def instance() -> str:
    return (os.getenv("EVOLUTION_INSTANCE", "") or "").strip()


def integration() -> str:
    return (os.getenv("EVOLUTION_INTEGRATION", "") or CLOUD_API).strip().upper()


def config_status() -> tuple[bool, str]:
    """What is missing, named precisely, so the answer is actionable."""
    if not enabled():
        return False, "EVOLUTION_ENABLED is not 1 — WhatsApp sending is off"
    if integration() == BAILEYS:
        return False, (
            "EVOLUTION_INTEGRATION is WHATSAPP-BAILEYS. That drives a real "
            "WhatsApp account over an unofficial protocol: no template "
            "approval, no dependable delivery receipts, and a ban takes the "
            "business's WhatsApp presence with it. Use WHATSAPP-BUSINESS.")
    if integration() != CLOUD_API:
        return False, (f"EVOLUTION_INTEGRATION={integration()!r} is not "
                       f"recognised; expected {CLOUD_API}")
    missing = [k for k in ("EVOLUTION_API_KEY", "EVOLUTION_INSTANCE")
               if not (os.getenv(k) or "").strip()]
    if missing:
        return False, "not configured: " + ", ".join(missing)
    return True, f"{base_url()} instance={instance()} integration={CLOUD_API}"


def normalise_msisdn(raw: str) -> str:
    """Digits only, with India's country code when a bare 10-digit mobile
    is given. Evolution expects an E.164-style number without the plus."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 10:
        return "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    return digits


def extract_message_id(response_text: str) -> str:
    """Pull the provider's message id out of a response body.

    Evolution returns {"key": {"id": "..."}}; Meta and BSP proxies in front of
    it use messageId / message_id / wamid. All are accepted, because the exact
    shape depends on which Evolution version and which proxy is in the path,
    and guessing wrong here means a real send gets reported as a failure.

    The first version of this caught only ValueError, so a response object
    without .json() raised AttributeError straight past it and a successful
    send was reported as failed. Parsing the text avoids depending on the
    response object having any particular method.
    """
    import json

    try:
        body = json.loads(response_text or "{}")
    except (ValueError, TypeError):
        return ""
    if not isinstance(body, dict):
        return ""

    key = body.get("key")
    if isinstance(key, dict) and str(key.get("id") or "").strip():
        return str(key["id"]).strip()

    for name in ("messageId", "message_id", "id", "wamid"):
        value = body.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def send_template(phone: str, template: str, *, params: list[str] | None = None,
                  language: str = "en", timeout: float = 20.0) -> SendResult:
    """Send ONE approved template message. The only way out of this system.

    Consent is not checked here and must not be: whatsapp_sender.consent_check
    owns that and runs before this is called. A transport that re-checks
    consent becomes a second authority, and two authorities on one question
    eventually disagree — which is how a WhatsApp path shipped with no consent
    check at all.
    """
    ok, detail = config_status()
    if not ok:
        return SendResult(status="not_configured", reason=detail)

    number = normalise_msisdn(phone)
    if len(number) < 11:
        return SendResult(status="blocked",
                          reason=f"no usable WhatsApp number: {phone!r}")

    if not (template or "").strip():
        return SendResult(
            status="blocked",
            reason=("no template named. Meta permits a business-initiated "
                    "conversation only through an approved template; free text "
                    "is refused here rather than attempted and rejected."))

    url = f"{base_url()}/message/sendTemplate/{instance()}"
    payload: dict[str, Any] = {
        "number": number,
        "name": template.strip(),
        "language": language,
    }
    if params:
        payload["components"] = [{
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in params],
        }]

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers={
                "apikey": (os.getenv("EVOLUTION_API_KEY") or "").strip(),
                "Content-Type": "application/json",
            })
        body = (r.text or "")[:1000]
        if r.status_code // 100 != 2:
            return SendResult(status="failed",
                              reason=f"Evolution HTTP {r.status_code}",
                              response=body[:300])

        # A 2xx without a message id is not a sent message. The Bolna and
        # Nuraveda adapters learned the same thing: a provider that answers
        # politely has not necessarily done anything.
        message_id = extract_message_id(body)

        if not message_id:
            return SendResult(
                status="failed",
                reason="Evolution returned 2xx with no message id — nothing "
                       "proves a message was queued",
                response=body[:300])

        return SendResult(
            status="sent", message_id=message_id, response=body[:300],
            provider_accepted=True, delivery_confirmed=False,
            reason=("Evolution accepted the send; delivery and read must be "
                    "confirmed by webhook, not assumed from this response"))
    except Exception as exc:  # noqa: BLE001 — unreachable is a status, not a crash
        return SendResult(status="failed",
                          reason=f"{type(exc).__name__}: {str(exc)[:120]}")


def health() -> dict[str, Any]:
    """Is the sidecar up, and is it in Cloud API mode? Reported verbatim."""
    ok, detail = config_status()
    out = {"configured": ok, "detail": detail, "integration": integration(),
           "url": base_url(), "instance": instance(), "reachable": False}
    if not enabled():
        return out
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{base_url()}/instance/connectionState/{instance()}",
                           headers={"apikey": (os.getenv("EVOLUTION_API_KEY") or "").strip()})
        out["reachable"] = r.status_code // 100 == 2
        out["http_status"] = r.status_code
        out["body"] = (r.text or "")[:300]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out
