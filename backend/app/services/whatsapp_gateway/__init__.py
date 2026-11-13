"""
WhatsApp Gateway — Klaviyo → AiSensy middleware for Pure Pantry Provisions.

Extends the existing whatsapp_sender module. Does NOT replace it.

Phase 2: consent, idempotency, client, status normaliser.
Phase 3: production-fail-closed webhook auth, sandbox path, concurrent safety.
"""

from app.services.whatsapp_gateway.client import WhatsAppGatewayClient, AiSensyClient, ProviderResult
from app.services.whatsapp_gateway.consent import ConsentDecision, check_whatsapp_marketing_consent
from app.services.whatsapp_gateway.idempotency import IdempotencyLedger
from app.services.whatsapp_gateway.router import CampaignRouter, resolve_campaign
from app.services.whatsapp_gateway.status_processor import StatusNormalizer, NormalizedStatus
from app.services.whatsapp_gateway.security import verify_webhook, require_admin_secret, is_production
from app.services.whatsapp_gateway.sandbox import run_sandbox_send, SandboxResult

__all__ = [
    "WhatsAppGatewayClient",
    "AiSensyClient",
    "ProviderResult",
    "ConsentDecision",
    "check_whatsapp_marketing_consent",
    "IdempotencyLedger",
    "CampaignRouter",
    "resolve_campaign",
    "StatusNormalizer",
    "NormalizedStatus",
    "verify_webhook",
    "require_admin_secret",
    "is_production",
    "run_sandbox_send",
    "SandboxResult",
]
