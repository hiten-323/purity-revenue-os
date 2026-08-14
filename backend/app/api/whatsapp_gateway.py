"""
WhatsApp Gateway HTTP endpoints.

POST /webhooks/klaviyo/whatsapp  — accept a send request from Klaviyo (or tests)
POST /webhooks/aisensy/status    — ingest delivery/read/failed callbacks

These endpoints are NOT wired into any live Klaviyo flow yet.
They exist so the gateway can be tested end-to-end with mocks first.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.services.whatsapp_gateway.client import AiSensyClient, normalise_msisdn
from app.services.whatsapp_gateway.consent import check_whatsapp_marketing_consent
from app.services.whatsapp_gateway.idempotency import IdempotencyLedger
from app.services.whatsapp_gateway.models import WhatsAppSendRequest
from app.services.whatsapp_gateway.router import CampaignRouter
from app.services.whatsapp_gateway.status_processor import StatusNormalizer

logger = logging.getLogger("whatsapp_gateway.api")

router = APIRouter(prefix="/webhooks", tags=["whatsapp-gateway"])


def _safe_log_extra(**kwargs) -> dict:
    """Strip anything that could contain secrets before logging."""
    blocked = {"api_key", "apikey", "authorization", "password", "secret", "token"}
    return {k: v for k, v in kwargs.items() if k.lower() not in blocked}


def _parse_klaviyo_body(body: dict[str, Any]) -> WhatsAppSendRequest:
    """
    Normalise an incoming JSON body into WhatsAppSendRequest.

    Accepts a flexible shape because the final Klaviyo webhook contract is not
    locked yet. Preferred top-level keys are documented in the response schema.
    """
    # Support both flat and nested "data" / "profile" shapes
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}

    profile_id = str(
        data.get("profile_id")
        or profile.get("id")
        or body.get("profile_id")
        or ""
    ).strip()

    phone = (
        data.get("phone")
        or data.get("phone_number")
        or profile.get("phone_number")
        or body.get("phone")
    )

    lifecycle_stage = str(
        data.get("lifecycle_stage")
        or data.get("stage")
        or body.get("lifecycle_stage")
        or ""
    ).strip()

    source_event_id = str(
        data.get("source_event_id")
        or data.get("event_id")
        or body.get("source_event_id")
        or ""
    ).strip()

    campaign_name = data.get("campaign_name") or data.get("campaign") or body.get("campaign_name")
    order_id = data.get("order_id") or body.get("order_id")
    user_name = (
        data.get("user_name")
        or data.get("first_name")
        or profile.get("first_name")
        or body.get("user_name")
    )

    template_params = data.get("template_params") or body.get("template_params")
    if template_params is not None and not isinstance(template_params, list):
        template_params = [str(template_params)]

    consent = (
        data.get("whatsapp_marketing_consent")
        or data.get("consent")
        or body.get("whatsapp_marketing_consent")
    )
    # Nested Klaviyo-style subscriptions.whatsapp.marketing.consent
    subs = data.get("subscriptions") or profile.get("subscriptions") or {}
    if isinstance(subs, dict):
        wa = subs.get("whatsapp") or {}
        if isinstance(wa, dict):
            marketing = wa.get("marketing") or {}
            if isinstance(marketing, dict) and marketing.get("consent"):
                consent = marketing.get("consent")

    return WhatsAppSendRequest(
        profile_id=profile_id,
        phone=phone,
        lifecycle_stage=lifecycle_stage,
        source_event_id=source_event_id,
        campaign_name=campaign_name,
        order_id=str(order_id) if order_id is not None else None,
        user_name=user_name,
        template_params=template_params,
        whatsapp_marketing_consent=str(consent) if consent is not None else None,
        raw=body,
    )


@router.post("/klaviyo/whatsapp")
async def klaviyo_whatsapp_send(request: Request, db: Session = Depends(get_db)):
    """
    Accept a WhatsApp send request (from Klaviyo webhook or test harness).

    Flow:
      1. Validate required fields
      2. Consent gate (phone alone is never enough)
      3. Idempotency claim
      4. Campaign resolution
      5. AiSensy send (only if new claim)
      6. Record result

    Response codes:
      200 — processed (sent, blocked, or duplicate)
      400 — malformed request
      500 — unexpected error
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON body"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "body must be a JSON object"})

    req = _parse_klaviyo_body(body)
    errors = req.validate_required()
    if errors:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "validation_failed", "details": errors},
        )

    phone_norm = normalise_msisdn(req.phone)
    ledger = IdempotencyLedger(db)
    router_ = CampaignRouter()

    # --- Consent gate (before any provider call) ---
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
        logger.info(
            "wa_blocked_consent key=%s stage=%s reason=%s",
            req.idempotency_key,
            req.lifecycle_stage,
            decision.reason,
        )
        return {
            "ok": True,
            "action": "blocked",
            "reason": decision.reason,
            "idempotency_key": req.idempotency_key,
            "status": "BLOCKED_CONSENT",
        }

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
        return {
            "ok": True,
            "action": "blocked",
            "reason": "missing or invalid phone",
            "idempotency_key": req.idempotency_key,
            "status": "BLOCKED_PHONE",
        }

    campaign = router_.resolve(req.lifecycle_stage, req.campaign_name)
    if not campaign:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "unknown_campaign",
                "lifecycle_stage": req.lifecycle_stage,
                "known_stages": router_.known_stages(),
            },
        )

    # --- Idempotency claim ---
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
        logger.info(
            "wa_duplicate_suppressed key=%s prior_status=%s message_id=%s",
            req.idempotency_key,
            row.status,
            row.aisensy_message_id or "",
        )
        return {
            "ok": True,
            "action": "duplicate",
            "idempotency_key": req.idempotency_key,
            "status": row.status,
            "aisensy_message_id": row.aisensy_message_id,
        }

    if not is_new and row.status in {"BLOCKED_CONSENT", "BLOCKED_PHONE", "FAILED", "PENDING"}:
        # Prior attempt blocked or failed — do not auto-retry from this endpoint.
        return {
            "ok": True,
            "action": "already_recorded",
            "idempotency_key": req.idempotency_key,
            "status": row.status,
            "error": row.error,
        }

    # --- Provider call ---
    client = AiSensyClient()
    result = client.send(
        campaign_name=campaign,
        destination=phone_norm,
        user_name=req.user_name,
        template_params=req.template_params or [req.user_name],
    )

    if result.provider_accepted:
        ledger.mark_provider_accepted(
            row,
            message_id=result.message_id,
            campaign_name=campaign,
        )
        logger.info(
            "wa_provider_accepted key=%s campaign=%s message_id=%s",
            req.idempotency_key,
            campaign,
            result.message_id or "",
        )
        return {
            "ok": True,
            "action": "sent",
            "idempotency_key": req.idempotency_key,
            "status": "PROVIDER_ACCEPTED",
            "aisensy_message_id": result.message_id or None,
            "delivery_confirmed": False,
            "note": "HTTP acceptance is not delivery confirmation",
        }

    ledger.mark_failed(row, result.reason)
    logger.info(
        "wa_provider_failed key=%s reason=%s",
        req.idempotency_key,
        result.reason,
    )
    return {
        "ok": True,
        "action": "failed",
        "idempotency_key": req.idempotency_key,
        "status": result.status,
        "reason": result.reason,
    }


@router.post("/aisensy/status")
async def aisensy_status_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Ingest delivery / read / failed callbacks from AiSensy.

    Payload schema is still UNKNOWN. We normalise best-effort and store the
    raw payload on the matching ledger row for later schema confirmation.
    """
    try:
        body = await request.json()
    except Exception:
        # Some providers send form-encoded; try raw text.
        try:
            raw = await request.body()
            body = raw.decode("utf-8", errors="replace")
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "unreadable body"})

    normalizer = StatusNormalizer()
    normalised = normalizer.normalise(body)

    raw_for_storage = None
    try:
        raw_for_storage = json.dumps(body if isinstance(body, dict) else {"raw": str(body)[:4000]})
    except Exception:
        raw_for_storage = str(body)[:4000]

    ledger = IdempotencyLedger(db)
    row = ledger.apply_status(
        message_id=normalised.message_id,
        status=normalised.status,
        raw_payload=raw_for_storage,
    )

    logger.info(
        "wa_status_received status=%s message_id=%s matched=%s notes=%s",
        normalised.status,
        normalised.message_id or "",
        bool(row),
        normalised.parse_notes,
    )

    return {
        "ok": True,
        "normalised_status": normalised.status,
        "message_id": normalised.message_id,
        "ledger_matched": bool(row),
        "ledger_status": row.status if row else None,
        "parse_notes": normalised.parse_notes,
        "schema_verified": False,  # remains False until real AiSensy payload is confirmed
    }
