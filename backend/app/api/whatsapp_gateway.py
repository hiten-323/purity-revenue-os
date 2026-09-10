"""
WhatsApp Gateway HTTP endpoints.

POST /api/v1/webhooks/klaviyo/whatsapp   — Klaviyo → gateway send (auth required in prod)
POST /api/v1/webhooks/aisensy/status     — AiSensy delivery/read callbacks (auth in prod)
POST /api/v1/webhooks/sandbox/whatsapp   — isolated mock path; never calls AiSensy
GET  /api/v1/webhooks/gateway/ledger     — admin-only ledger lookup

These endpoints are NOT wired into any live Klaviyo flow.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.services.whatsapp_gateway.client import WhatsAppGatewayClient, normalise_msisdn
from app.services.whatsapp_gateway.consent import check_whatsapp_marketing_consent
from app.services.whatsapp_gateway.idempotency import IdempotencyLedger
from app.services.whatsapp_gateway.models import WhatsAppSendLedger, WhatsAppSendRequest
from app.services.whatsapp_gateway.router import CampaignRouter
from app.services.whatsapp_gateway.sandbox import run_sandbox_send
from app.services.whatsapp_gateway.security import require_admin_secret, verify_webhook
from app.services.whatsapp_gateway.status_processor import StatusNormalizer

logger = logging.getLogger("whatsapp_gateway.api")

router = APIRouter(prefix="/webhooks", tags=["whatsapp-gateway"])


def _headers_dict(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items()}


def _parse_klaviyo_body(body: dict[str, Any]) -> WhatsAppSendRequest:
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}

    profile_id = str(
        data.get("profile_id") or profile.get("id") or body.get("profile_id") or ""
    ).strip()

    phone = (
        data.get("phone")
        or data.get("phone_number")
        or profile.get("phone_number")
        or body.get("phone")
    )

    lifecycle_stage = str(
        data.get("lifecycle_stage") or data.get("stage") or body.get("lifecycle_stage") or ""
    ).strip()

    source_event_id = str(
        data.get("source_event_id") or data.get("event_id") or body.get("source_event_id") or ""
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
    raw_body = await request.body()
    auth = verify_webhook(
        provider="klaviyo",
        headers=_headers_dict(request),
        body=raw_body,
        secret_env_var="KLAVIYO_WEBHOOK_SECRET",
    )
    if not auth.allowed:
        logger.warning("wa_klaviyo_auth_rejected reason=%s mode=%s", auth.reason, auth.mode)
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "unauthorized", "detail": auth.reason},
        )

    try:
        body = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
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
        return {
            "ok": True,
            "action": "already_recorded",
            "idempotency_key": req.idempotency_key,
            "status": row.status,
            "error": row.error,
        }

    client = WhatsAppGatewayClient()
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
    logger.info("wa_provider_failed key=%s reason=%s", req.idempotency_key, result.reason)
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

    Payload schema is still UNKNOWN. Adaptive normaliser + raw retention.
    Auth: AISENSY_WEBHOOK_SECRET mandatory in production if set; if unset in
    production we still reject (fail closed via verify_webhook when secret
    required). For AiSensy, set AISENSY_WEBHOOK_SECRET once you know how
    AiSensy signs or shares a secret; until then production should not expose
    this path publicly without a reverse-proxy shared secret.
    """
    raw_body = await request.body()
    auth = verify_webhook(
        provider="aisensy",
        headers=_headers_dict(request),
        body=raw_body,
        secret_env_var="AISENSY_WEBHOOK_SECRET",
    )
    if not auth.allowed:
        logger.warning("wa_aisensy_auth_rejected reason=%s mode=%s", auth.reason, auth.mode)
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "unauthorized", "detail": auth.reason},
        )

    try:
        body = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except Exception:
        try:
            body = raw_body.decode("utf-8", errors="replace")
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "error": "unreadable body"})

    normalizer = StatusNormalizer()
    normalised = normalizer.normalise(body)

    try:
        raw_for_storage = json.dumps(
            body if isinstance(body, dict) else {"raw": str(body)[:4000]}
        )
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
        "schema_verified": False,
    }


@router.post("/sandbox/whatsapp")
async def sandbox_whatsapp_send(request: Request, db: Session = Depends(get_db)):
    """
    Isolated test path. Always mocks AiSensy. Never sends a real message.

    Auth uses KLAVIYO_WEBHOOK_SECRET (same as production path) so only callers
    with the secret can exercise the sandbox on a deployed host.
    """
    raw_body = await request.body()
    auth = verify_webhook(
        provider="sandbox",
        headers=_headers_dict(request),
        body=raw_body,
        secret_env_var="KLAVIYO_WEBHOOK_SECRET",
    )
    if not auth.allowed:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "unauthorized", "detail": auth.reason},
        )

    try:
        body = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid JSON body"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "body must be a JSON object"})

    req = _parse_klaviyo_body(body)
    result = run_sandbox_send(db, req)
    return {
        "ok": result.ok,
        "action": result.action,
        "status": result.status,
        "reason": result.reason,
        "idempotency_key": result.idempotency_key,
        "fake_message_id": result.fake_message_id or None,
        "campaign_name": result.campaign_name,
        "sandbox": True,
        "note": "No real WhatsApp message was sent",
    }


@router.get("/gateway/ledger")
async def gateway_ledger_lookup(
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Query(None),
    message_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """Admin-only ledger inspection. Never public without GATEWAY_ADMIN_SECRET."""
    auth = require_admin_secret(_headers_dict(request))
    if not auth.allowed:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "unauthorized", "detail": auth.reason},
        )

    q = db.query(WhatsAppSendLedger)
    if idempotency_key:
        q = q.filter(WhatsAppSendLedger.idempotency_key == idempotency_key)
    if message_id:
        q = q.filter(WhatsAppSendLedger.aisensy_message_id == message_id)
    rows = q.order_by(WhatsAppSendLedger.id.desc()).limit(limit).all()

    return {
        "ok": True,
        "count": len(rows),
        "rows": [
            {
                "idempotency_key": r.idempotency_key,
                "profile_id": r.profile_id,
                "lifecycle_stage": r.lifecycle_stage,
                "campaign_name": r.campaign_name,
                "status": r.status,
                "aisensy_message_id": r.aisensy_message_id,
                "order_id": r.order_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
                "read_at": r.read_at.isoformat() if r.read_at else None,
                "failed_at": r.failed_at.isoformat() if r.failed_at else None,
                "error": r.error,
                # phone intentionally omitted from default listing to reduce PII exposure
            }
            for r in rows
        ],
    }
