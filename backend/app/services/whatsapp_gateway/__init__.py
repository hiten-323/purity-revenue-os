"""
WhatsApp Gateway — Klaviyo → AiSensy middleware for Pure Pantry Provisions.

Extends the existing whatsapp_sender module. Does NOT replace it.

Responsibilities:
- Accept Klaviyo webhook payloads requesting a WhatsApp send
- Enforce native WhatsApp marketing consent (phone alone is NEVER enough)
- Enforce persistent idempotency so the same logical event cannot produce two sends
- Call AiSensy via the existing client patterns
- Ingest delivery/read/failed status callbacks (payload schema still UNKNOWN)

This package is intentionally isolated from live Klaviyo flows until Phase 3+.
"""

from app.services.whatsapp_gateway.client import AiSensyClient, ProviderResult
from app.services.whatsapp_gateway.consent import ConsentDecision, check_whatsapp_marketing_consent
from app.services.whatsapp_gateway.idempotency import IdempotencyLedger
from app.services.whatsapp_gateway.router import CampaignRouter, resolve_campaign
from app.services.whatsapp_gateway.status_processor import StatusNormalizer, NormalizedStatus

__all__ = [
    "AiSensyClient",
    "ProviderResult",
    "ConsentDecision",
    "check_whatsapp_marketing_consent",
    "IdempotencyLedger",
    "CampaignRouter",
    "resolve_campaign",
    "StatusNormalizer",
    "NormalizedStatus",
]
