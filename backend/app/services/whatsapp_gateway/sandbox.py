"""
Isolated sandbox path for the WhatsApp Gateway.

Hard rules:
- Never calls the real AiSensy network.
- Never uses a real customer phone as a default destination.
- Always records ledger rows with status SANDBOX_* so production analytics
  can exclude them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.whatsapp_gateway.client import normalise_msisdn
from app.services.whatsapp_gateway.consent import check_whatsapp_marketing_consent
from app.services.whatsapp_gateway.idempotency import IdempotencyLedger
from app.services.whatsapp_gateway.models import WhatsAppSendRequest
from app.services.whatsapp_gateway.router import CampaignRouter


@dataclass
class SandboxResult:
    ok: bool
    action: str
    status: str
    reason: str = ""
    idempotency_key: str = ""
    fake_message_id: str = ""
    campaign_name: str | None = None


def run_sandbox_send(db: Session, req: WhatsAppSendRequest) -> SandboxResult:
    """
    Execute the full consent → idempotency → campaign path without network I/O.
    """
    errors = req.validate_required()
    if errors:
        return SandboxResult(
            ok=False,
            action="validation_failed",
            status="ERROR",
            reason="; ".join(errors),
        )

    phone_norm = normalise_msisdn(req.phone)
    ledger = IdempotencyLedger(db)
    router = CampaignRouter()

    decision = check_whatsapp_marketing_consent(req.whatsapp_marketing_consent)
    if not decision.allowed:
        row, is_new = ledger.begin_send(
            profile_id=req.profile_id,
            phone=phone_norm or None,
            lifecycle_stage=req.lifecycle_stage,
            source_event_id=req.source_event_id,
            campaign_name=req.campaign_name,
            order_id=req.order_id,
            initial_status="BLOCKED_CONSENT",
        )
        if is_new:
            ledger.mark_blocked(row, "BLOCKED_CONSENT", decision.reason)
        return SandboxResult(
            ok=True,
            action="blocked",
            status="BLOCKED_CONSENT",
            reason=decision.reason,
            idempotency_key=req.idempotency_key,
        )

    if len(phone_norm) < 11:
        row, is_new = ledger.begin_send(
            profile_id=req.profile_id,
            phone=phone_norm or None,
            lifecycle_stage=req.lifecycle_stage,
            source_event_id=req.source_event_id,
            campaign_name=req.campaign_name,
            order_id=req.order_id,
            initial_status="BLOCKED_PHONE",
        )
        if is_new:
            ledger.mark_blocked(row, "BLOCKED_PHONE", "missing or invalid phone")
        return SandboxResult(
            ok=True,
            action="blocked",
            status="BLOCKED_PHONE",
            reason="missing or invalid phone",
            idempotency_key=req.idempotency_key,
        )

    campaign = router.resolve(req.lifecycle_stage, req.campaign_name)
    if not campaign:
        return SandboxResult(
            ok=False,
            action="unknown_campaign",
            status="ERROR",
            reason=f"unknown lifecycle_stage={req.lifecycle_stage}",
            idempotency_key=req.idempotency_key,
        )

    row, is_new = ledger.begin_send(
        profile_id=req.profile_id,
        phone=phone_norm,
        lifecycle_stage=req.lifecycle_stage,
        source_event_id=req.source_event_id,
        campaign_name=campaign,
        order_id=req.order_id,
        initial_status="PENDING",
    )

    if not is_new and IdempotencyLedger.already_sent(row):
        return SandboxResult(
            ok=True,
            action="duplicate",
            status=row.status,
            reason="duplicate suppressed",
            idempotency_key=req.idempotency_key,
            fake_message_id=row.aisensy_message_id or "",
            campaign_name=campaign,
        )

    if not is_new and row.status in {"BLOCKED_CONSENT", "BLOCKED_PHONE", "FAILED", "PENDING"}:
        return SandboxResult(
            ok=True,
            action="already_recorded",
            status=row.status,
            reason=row.error or "",
            idempotency_key=req.idempotency_key,
            campaign_name=campaign,
        )

    # Simulated provider acceptance — no network.
    fake_mid = f"sandbox.wamid.{uuid.uuid4().hex[:16]}"
    ledger.mark_provider_accepted(row, message_id=fake_mid, campaign_name=campaign)

    return SandboxResult(
        ok=True,
        action="sandbox_sent",
        status="PROVIDER_ACCEPTED",
        reason="sandbox mock — no real AiSensy call",
        idempotency_key=req.idempotency_key,
        fake_message_id=fake_mid,
        campaign_name=campaign,
    )
