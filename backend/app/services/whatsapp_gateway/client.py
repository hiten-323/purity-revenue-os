"""
WhatsApp Gateway transport client (Evolution API, Meta Cloud API mode).

Extends patterns from app.services.whatsapp_sender (MSISDN normalisation,
message-id extraction, provider-accepted vs delivered distinction).
Does NOT duplicate the B2B lead consent logic — that stays in whatsapp_sender.

Never logs or returns the API key.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

# No module-level endpoint any more, deliberately. Three modules each held
# a copy of AISENSY_URL and each POSTed to it; the endpoint being a
# convenient constant is what made adding a third transport trivial.
# whatsapp_evolution owns the destination now.

logger = logging.getLogger("whatsapp_gateway.client")


@dataclass
class ProviderResult:
    """
    Outcome of a single WhatsApp send via the Evolution transport.

    status values:
      PROVIDER_ACCEPTED  — HTTP 2xx; the provider accepted the request
      FAILED             — HTTP non-2xx or transport error
      NOT_CONFIGURED     — EVOLUTION_API_KEY missing
      INVALID_REQUEST    — missing campaign / phone before the call

    delivery_confirmed is ALWAYS False here. Delivery/read must come from
    the status webhook, not from HTTP 200.
    """

    status: str
    reason: str = ""
    message_id: str = ""  # wamid when extractable
    http_status: int | None = None
    provider_accepted: bool = False
    delivery_confirmed: bool = False
    raw_response_snippet: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _api_key() -> str:
    return (os.getenv("EVOLUTION_API_KEY") or "").strip()


def is_configured() -> bool:
    key = _api_key()
    return bool(key and key not in ("", "your_evolution_api_key_here"))


def normalise_msisdn(raw: str | None) -> str:
    """Country-coded digits only. Bare 10-digit Indian mobiles get 91 prefix."""
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(d) == 10:
        d = "91" + d
    return d


def extract_message_id(response_text: str, headers: Any) -> str:
    """
    Extract provider message id without assuming one transport shape.
    Prefer header, then common JSON keys used by AiSensy / Meta BSPs.
    """
    try:
        header_id = str(
            (headers.get("x-message-id") if headers else None)
            or (headers.get("x-messageid") if headers else None)
            or ""
        ).strip()
    except Exception:
        header_id = ""
    if header_id:
        return header_id

    try:
        body = json.loads(response_text or "{}")
    except Exception:
        return ""

    if not isinstance(body, dict):
        return ""

    for key in ("messageId", "message_id", "id", "wamid"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    # Nested shapes seen in some BSP responses
    for container_key in ("data", "messages", "result"):
        container = body.get(container_key)
        if isinstance(container, dict):
            for key in ("messageId", "message_id", "id", "wamid"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(container, list) and container:
            first = container[0]
            if isinstance(first, dict):
                for key in ("messageId", "message_id", "id", "wamid"):
                    value = first.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    return ""


class WhatsAppGatewayClient:
    """
    Thin client around the send endpoint.

    Named AiSensyClient until the transport was consolidated. The alias
    below keeps existing imports working; the provider is no longer part
    of the name because it should not have been.

    All network I/O is mockable by injecting `http_client` (tests do this).
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        http_client: Optional[Any] = None,
        max_retries: int = 0,
    ):
        self.timeout = timeout
        self._http_client = http_client
        self.max_retries = max_retries

    def send(
        self,
        *,
        campaign_name: str,
        destination: str,
        user_name: str = "there",
        template_params: list[str] | None = None,
        source: str = "purity-whatsapp-gateway",
    ) -> ProviderResult:
        if not is_configured():
            return ProviderResult(
                status="NOT_CONFIGURED",
                reason="EVOLUTION_API_KEY not set",
            )

        campaign = (campaign_name or "").strip()
        if not campaign:
            return ProviderResult(
                status="INVALID_REQUEST",
                reason="campaign_name is required",
            )

        phone = normalise_msisdn(destination)
        if len(phone) < 11:
            return ProviderResult(
                status="INVALID_REQUEST",
                reason="destination phone is missing or invalid after normalisation",
            )

        # Evolution API in Meta Cloud API mode, resolved at call time from
        # whatsapp_evolution — the one module that owns where a WhatsApp
        # message goes. `campaign` is the Meta-approved template name.
        from app.services import whatsapp_evolution as transport

        url = f"{transport.base_url()}/message/sendTemplate/{transport.instance()}"
        headers_out = {"apikey": (os.getenv("EVOLUTION_API_KEY") or "").strip(),
                       "Content-Type": "application/json"}
        payload: dict[str, Any] = {
            "number": phone,
            "name": campaign,
            "language": "en",
        }
        params = template_params if template_params is not None else [user_name or "there"]
        if params:
            payload["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params],
            }]

        # Log only safe fields — never the API key.
        logger.info(
            "whatsapp_send_attempt template=%s destination_len=%s source=%s",
            campaign,
            len(phone),
            source,
        )

        try:
            if self._http_client is not None:
                # Injected client (tests / custom transport)
                r = self._http_client.post(url, json=payload, headers=headers_out)
            else:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(url, json=payload, headers=headers_out)

            body = (getattr(r, "text", None) or "")[:1000]
            status_code = int(getattr(r, "status_code", 0) or 0)
            headers = getattr(r, "headers", {}) or {}

            if 200 <= status_code < 300:
                mid = extract_message_id(body, headers)
                return ProviderResult(
                    status="PROVIDER_ACCEPTED",
                    reason="Evolution accepted the send request; delivery must be confirmed by status webhook",
                    message_id=mid,
                    http_status=status_code,
                    provider_accepted=True,
                    delivery_confirmed=False,
                    raw_response_snippet=body[:300],
                )

            return ProviderResult(
                status="FAILED",
                reason=f"Evolution HTTP {status_code}",
                http_status=status_code,
                raw_response_snippet=body[:300],
            )
        except Exception as e:
            # Never include exception args that might echo the request body.
            return ProviderResult(
                status="FAILED",
                reason=f"{type(e).__name__}: transport or timeout error",
            )


# Back-compat: this class was AiSensyClient when there were three AiSensy
# transports. Kept so existing imports do not break.
AiSensyClient = WhatsAppGatewayClient
