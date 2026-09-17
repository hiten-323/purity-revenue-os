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
# whatsapp_aisensy owns the destination now.

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


# Asked of the transport, not answered here. This module used to read
# EVOLUTION_API_KEY directly, which meant "can we send?" had two answers the
# moment the transport changed -- and it changed.
def is_configured() -> bool:
    from app.services import whatsapp_aisensy as transport
    return transport.config_status()[0]


# Imported, not reimplemented. This module's own version left the national
# trunk prefix in place: "09876543210" came out as "09876543210" rather than
# "919876543210", so the gateway path sent to a destination the other path
# would never have produced.
from app.services.identity import msisdn as normalise_msisdn


# The transport owns this. A second copy here had already drifted: it looked
# for key.id and id but not messageId or wamid, so a successful send with a
# differently-shaped body read as a failure.
from app.services.whatsapp_aisensy import extract_message_id


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
        """Delegate to the one transport and translate its answer.

        This method used to build its own URL, headers and payload out of
        whatsapp_evolution's base_url. Borrowing another module's endpoint is
        not the same as sharing its implementation: it was still a second
        request, with its own idea of the payload shape, one edit away from
        drifting. Now there is exactly one place a WhatsApp request is
        constructed, and this translates SendResult into the ledger's
        vocabulary.

        Consent is NOT checked here. The gateway's own consent module governs
        its Klaviyo-sourced callers; whatsapp_sender governs B2B leads. Adding
        a third opinion in the transport layer is the bug this codebase has
        produced eight times.
        """
        from app.services import whatsapp_aisensy as transport

        campaign = (campaign_name or "").strip()
        if not campaign:
            return ProviderResult(
                status="INVALID_REQUEST",
                reason="campaign_name is required",
            )

        # Log only safe fields — never the API key.
        logger.info(
            "whatsapp_send_attempt campaign=%s destination_len=%s source=%s",
            campaign,
            len(normalise_msisdn(destination)),
            source,
        )

        result = transport.send_template(
            destination, campaign,
            params=template_params if template_params is not None else [user_name or "there"],
            timeout=self.timeout,
            user_name=user_name,
            client=self._http_client,
        )

        if result.status == "sent":
            return ProviderResult(
                status="PROVIDER_ACCEPTED",
                reason=result.reason,
                message_id=result.message_id,
                http_status=result.http_status,
                provider_accepted=True,
                delivery_confirmed=False,
                raw_response_snippet=result.response[:300],
            )
        if result.status == "not_configured":
            return ProviderResult(status="NOT_CONFIGURED", reason=result.reason)
        if result.status == "blocked":
            return ProviderResult(status="INVALID_REQUEST", reason=result.reason)
        return ProviderResult(
            status="FAILED",
            reason=result.reason,
            http_status=result.http_status,
            raw_response_snippet=result.response[:300],
        )



# Back-compat: this class was AiSensyClient when there were three AiSensy
# transports. Kept so existing imports do not break.
AiSensyClient = WhatsAppGatewayClient
