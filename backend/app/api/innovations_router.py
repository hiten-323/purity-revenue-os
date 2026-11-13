"""
FastAPI Router for Inbound Lead Speed-to-Lead, Persistent Memory & Paperclip Governance.
Purity Revenue OS V2.0.
"""
from __future__ import annotations

import logging
import hashlib
import hmac
import os
import time
import collections
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.services.inbound_lead_engine import InboundSpeedToLeadEngine, InboundLeadPayload
from app.services.persistent_memory import PersistentMemoryEngine
from app.services.agent_governance import AgentGovernanceEngine

_log = logging.getLogger("innovations_router")
router = APIRouter(tags=["inbound-speed-memory-governance"])

_PROCESSED_WEBHOOK_EVENTS = collections.OrderedDict()
_REPLAY_WINDOW_SECONDS = 300
_MAX_STORED_EVENTS = 10000


def _record_and_check_replay(key: str, now_ts: float) -> bool:
    cutoff = now_ts - _REPLAY_WINDOW_SECONDS
    while _PROCESSED_WEBHOOK_EVENTS:
        k, ts = next(iter(_PROCESSED_WEBHOOK_EVENTS.items()))
        if ts < cutoff:
            _PROCESSED_WEBHOOK_EVENTS.popitem(last=False)
        else:
            break
    if key in _PROCESSED_WEBHOOK_EVENTS:
        return False
    _PROCESSED_WEBHOOK_EVENTS[key] = now_ts
    if len(_PROCESSED_WEBHOOK_EVENTS) > _MAX_STORED_EVENTS:
        _PROCESSED_WEBHOOK_EVENTS.popitem(last=False)
    return True


class InboundLeadRequest(BaseModel):
    name: str = Field(..., description="Contact person full name")
    phone: str = Field(..., description="10-digit mobile number or E.164")
    email: Optional[str] = Field(None, description="Email address")
    company: Optional[str] = Field(None, description="Business or brand name")
    city: Optional[str] = Field(None, description="City / state")
    source: str = Field("website_form", description="Source: website_form | instagram_ad | whatsapp | sample_request")
    inquiry_text: str = Field("", description="Raw customer message or requirements")
    preferred_format: Optional[str] = Field(None, description="jars | sachets | bulk")
    business_type: Optional[str] = Field(None, description="cafe | hotel | office | retailer | distributor | consumer")
    estimated_monthly_kg: Optional[float] = Field(None, description="Monthly volume in kg if stated")
    consent_explicit: bool = Field(False, description="Explicit channel consent checkbox captured on the source form")
    consent_text: Optional[str] = Field(None, description="Exact consent wording displayed and accepted by the lead")
    consent_channels: List[str] = Field(default_factory=list, description="Explicitly selected channels: voice, whatsapp, email")
    consent_evidence_url: Optional[str] = Field(None, description="Source form or consent record URL")


class FounderDecisionRequest(BaseModel):
    decision: str = Field(..., description="APPROVED or REJECTED")
    notes: Optional[str] = Field("", description="Founder notes or condition")


@router.post("/inbound/lead-webhook")
async def inbound_lead_webhook(
    req: InboundLeadRequest,
    request: Request,
    x_inbound_signature: Optional[str] = Header(None, alias="X-Inbound-Signature"),
    x_inbound_timestamp: Optional[str] = Header(None, alias="X-Inbound-Timestamp"),
    x_inbound_event_id: Optional[str] = Header(None, alias="X-Inbound-Event-Id"),
    db: Session = Depends(get_db)
):
    """
    Sub-60-second Speed-to-Lead Ingestion Endpoint (Bashar Jkatou model).
    Receives inbound inquiry and routes B2B leads to founder review.
    Enforces HMAC signature, timestamp freshness (< 300s), and replay protection.
    """
    webhook_secret = (os.getenv("INBOUND_WEBHOOK_SECRET") or "").strip()
    signature_valid = False

    if webhook_secret:
        if not x_inbound_signature:
            raise HTTPException(status_code=401, detail="Unauthorized: Missing X-Inbound-Signature header")

        now_ts = time.time()
        # 1. Timestamp validation (if provided)
        if x_inbound_timestamp:
            try:
                try:
                    req_ts = float(x_inbound_timestamp.strip())
                except ValueError:
                    req_ts = datetime.fromisoformat(x_inbound_timestamp.strip().replace("Z", "+00:00")).timestamp()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid X-Inbound-Timestamp format")

            if (now_ts - req_ts) > _REPLAY_WINDOW_SECONDS:
                raise HTTPException(status_code=400, detail="Webhook timestamp expired")
            if (req_ts - now_ts) > 60:
                raise HTTPException(status_code=400, detail="Webhook timestamp in future")

        # 2. Replay & Idempotency Protection
        received_sig = x_inbound_signature.strip()
        if received_sig.lower().startswith("sha256="):
            received_sig = received_sig[7:]

        event_key = f"{x_inbound_event_id or ''}:{received_sig.lower()}:{x_inbound_timestamp or ''}"
        if not _record_and_check_replay(event_key, now_ts):
            raise HTTPException(status_code=409, detail="Replay detected: webhook already processed")

        # 3. HMAC Verification against raw request body
        raw_body = await request.body()
        expected = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

        # Also support timestamp-prefixed HMAC: sha256(f"{ts}." + body)
        expected_ts = None
        if x_inbound_timestamp:
            ts_bytes = f"{x_inbound_timestamp.strip()}.".encode("utf-8")
            expected_ts = hmac.new(webhook_secret.encode("utf-8"), ts_bytes + raw_body, hashlib.sha256).hexdigest()

        if not (hmac.compare_digest(received_sig.lower(), expected.lower()) or 
                (expected_ts and hmac.compare_digest(received_sig.lower(), expected_ts.lower()))):
            raise HTTPException(status_code=403, detail="Forbidden: Invalid webhook signature")

        signature_valid = True

    try:
        payload = InboundLeadPayload(
            name=req.name,
            phone=req.phone,
            email=req.email,
            company=req.company,
            city=req.city,
            source=req.source,
            inquiry_text=req.inquiry_text,
            preferred_format=req.preferred_format,
            business_type=req.business_type,
            estimated_monthly_kg=req.estimated_monthly_kg,
            consent_explicit=req.consent_explicit,
            consent_text=req.consent_text,
            consent_channels=req.consent_channels,
            consent_evidence_url=req.consent_evidence_url,
            consent_signature_valid=signature_valid,
        )
        result = InboundSpeedToLeadEngine.process_inbound_lead(payload, db)

        return {
            "success": True,
            "status": result.status,
            "lead_id": result.lead_id,
            "company": result.company,
            "recommended_product": result.recommended_product,
            "recommended_sku": result.recommended_sku,
            "direct_checkout_link": result.direct_checkout_link,
            "immediate_message_draft": result.immediate_message_draft,
            "cadence_steps_count": len(result.cadence),
            "cadence": [
                {
                    "step": c.step_number,
                    "delay_minutes": c.delay_minutes,
                    "channel": c.channel,
                    "scheduled_for": c.scheduled_for,
                    "message_preview": c.message[:120] + "..."
                }
                for c in result.cadence
            ],
            "memory_extracted": result.memory_extracted,
            "governance_approved": result.governance_approved
        }
    except Exception as e:
        _log.error("Failed to process inbound lead: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Inbound processing error: {str(e)}")


@router.get("/leads/{lead_id}/memory")
async def get_lead_memory(
    lead_id: int,
    db: Session = Depends(get_db)
):
    """
    Claude-Mem Long-Term Persistent Memory Endpoint.
    Returns distilled cross-session historical context for a specific lead.
    """
    context = PersistentMemoryEngine.get_condensed_memory_context(lead_id, db)
    return {
        "lead_id": lead_id,
        "persistent_memory_context": context
    }


@router.get("/governance/status")
async def get_governance_status(
    db: Session = Depends(get_db)
):
    """
    Paperclip Agent Governance & Financial Budgets Status.
    Returns spent today, remaining budget, kill-switch state, and channel caps.
    """
    status = AgentGovernanceEngine.get_governance_status(db)
    return {
        "system_date": status.system_date,
        "daily_budget_inr": status.daily_budget_inr,
        "spent_today_inr": status.spent_today_inr,
        "budget_remaining_inr": status.budget_remaining_inr,
        "is_budget_exceeded": status.is_budget_exceeded,
        "kill_switch_active": status.kill_switch_active,
        "pending_founder_approvals_count": status.pending_founder_approvals_count,
        "channel_caps": status.channel_caps
    }


@router.get("/governance/approvals")
async def get_pending_approvals(
    db: Session = Depends(get_db)
):
    """
    Returns all deals and actions currently requiring Founder Approval (Paperclip tier gate).
    """
    from app.models.models import ApprovalRequest, B2BLead

    requests = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.status == "PENDING_FOUNDER")
        .order_by(ApprovalRequest.created_at.desc())
        .all()
    )

    items = []
    for r in requests:
        lead = db.query(B2BLead).filter(B2BLead.id == r.lead_id).first() if r.lead_id else None
        items.append({
            "id": r.id,
            "lead_id": r.lead_id,
            "company": lead.company if lead else "Unknown",
            "type": r.type,
            "status": r.status,
            "expected_revenue": r.expected_revenue,
            "expected_margin": r.expected_margin,
            "draft_subject": r.draft_subject,
            "draft_message": r.draft_message,
            "evidence": r.evidence,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })

    return {"pending_approvals": items, "count": len(items)}


@router.post("/governance/approvals/{request_id}/decision")
async def submit_founder_approval_decision(
    request_id: int,
    req: FounderDecisionRequest,
    db: Session = Depends(get_db)
):
    """
    Processes Founder Approval or Rejection for a held commercial deal or agent action.
    """
    result = AgentGovernanceEngine.process_founder_decision(
        request_id=request_id,
        decision=req.decision,
        founder_notes=req.notes or "",
        db=db
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to process decision"))
    return result
