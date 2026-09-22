"""Append-only Outreach Event Ledger. INSERT only; idempotent on idempotency_key."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.outreach_intelligence.models import OutreachEvent

log = logging.getLogger(__name__)
MODEL_VERSION = "oi-v1"


def ensure_outreach_intelligence_schema(engine) -> None:
    from app.database.database import Base
    import app.services.outreach_intelligence.models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def record_event(
    db: Session,
    *,
    event_type: str,
    lead_id: int | None = None,
    company_id: int | None = None,
    company: str | None = None,
    channel: str | None = None,
    occurred_at: datetime | None = None,
    campaign_source: str | None = None,
    message_version: str | None = None,
    template_version_id: str | None = None,
    personalization_json: dict | None = None,
    delivery_status: str | None = None,
    provider_status: str | None = None,
    response_status: str | None = None,
    outcome: str | None = None,
    confidence: str | None = None,
    next_action: str | None = None,
    model_version: str | None = None,
    metadata_json: dict | None = None,
    idempotency_key: str | None = None,
    commit: bool = False,
) -> OutreachEvent:
    if idempotency_key:
        existing = (
            db.query(OutreachEvent)
            .filter(OutreachEvent.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            return existing

    row = OutreachEvent(
        event_type=str(event_type),
        lead_id=lead_id,
        company_id=company_id,
        company=company,
        channel=channel,
        occurred_at=occurred_at or datetime.utcnow(),
        campaign_source=campaign_source,
        message_version=message_version,
        template_version_id=template_version_id,
        personalization_json=personalization_json,
        delivery_status=delivery_status,
        provider_status=provider_status,
        response_status=response_status,
        outcome=outcome,
        confidence=confidence,
        next_action=next_action,
        model_version=model_version or MODEL_VERSION,
        metadata_json=metadata_json,
        idempotency_key=idempotency_key,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


def mirror_workflow_event(db: Session, event) -> OutreachEvent | None:
    try:
        et = getattr(event, "event_type", None) or ""
        key = f"wf:{getattr(event, 'id', None)}"
        channel = getattr(event, "channel", None)
        if not channel:
            if "EMAIL" in et:
                channel = "email"
            elif "CALL" in et:
                channel = "call"
            else:
                channel = "system"
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        return record_event(
            db,
            event_type=et,
            lead_id=getattr(event, "lead_id", None),
            channel=channel,
            occurred_at=getattr(event, "occurred_at", None),
            outcome=str(payload.get("outcome") or payload.get("intent") or "") or None,
            confidence="OBSERVED",
            metadata_json={"source": "workflow_event", "payload": payload},
            idempotency_key=key if getattr(event, "id", None) is not None else None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("mirror_workflow_event failed: %s", exc)
        return None


def sync_from_existing(db: Session, *, limit: int = 500) -> dict[str, Any]:
    stats = {"workflow": 0, "touches": 0, "calls": 0, "errors": 0}
    try:
        from app.models.models import WorkflowEvent
        for ev in db.query(WorkflowEvent).order_by(WorkflowEvent.id.desc()).limit(limit).all():
            if mirror_workflow_event(db, ev) is not None:
                stats["workflow"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("sync workflow failed: %s", exc)
        stats["errors"] += 1

    try:
        from app.services.smart_outreach import OutreachTouch
        for t in db.query(OutreachTouch).order_by(OutreachTouch.id.desc()).limit(limit).all():
            record_event(
                db,
                event_type=f"TOUCH_{getattr(t, 'status', 'UNKNOWN')}",
                lead_id=t.lead_id,
                channel=getattr(t, "channel", None),
                occurred_at=getattr(t, "occurred_at", None),
                template_version_id=getattr(t, "template_key", None),
                delivery_status=getattr(t, "status", None),
                confidence="OBSERVED",
                metadata_json={
                    "source": "outreach_touch",
                    "touch_type": getattr(t, "touch_type", None),
                    "payload": getattr(t, "payload", None),
                },
                idempotency_key=f"touch:{t.id}",
            )
            stats["touches"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("sync touches failed: %s", exc)
        stats["errors"] += 1

    try:
        from app.models.models import B2BLead
        for lead in (
            db.query(B2BLead)
            .filter(B2BLead.call_outcome_last.isnot(None))
            .order_by(B2BLead.id.desc())
            .limit(limit)
            .all()
        ):
            outcome = str(lead.call_outcome_last or "").upper()
            record_event(
                db,
                event_type="CALL_OUTCOME_RECORDED",
                lead_id=lead.id,
                company=getattr(lead, "company", None),
                channel="call",
                outcome=outcome,
                confidence="OBSERVED",
                metadata_json={"source": "b2b_lead.call_outcome_last"},
                idempotency_key=f"call_outcome:{lead.id}:{outcome}",
            )
            stats["calls"] += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("sync calls failed: %s", exc)
        stats["errors"] += 1

    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("sync commit failed: %s", exc)
        db.rollback()
        stats["errors"] += 1
    return stats


def emit_email_sent(db: Session, *, lead_id: int, status: str = "SENT",
                    idempotency_key: str | None = None, **extra) -> OutreachEvent | None:
    try:
        return record_event(
            db, event_type="EMAIL_SENT", lead_id=lead_id, channel="email",
            delivery_status=status, confidence="OBSERVED",
            metadata_json=extra or None, idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("emit_email_sent failed: %s", exc)
        return None


def emit_call_outcome(db: Session, *, lead_id: int, outcome: str,
                      idempotency_key: str | None = None, **extra) -> OutreachEvent | None:
    try:
        return record_event(
            db, event_type="CALL_OUTCOME_RECORDED", lead_id=lead_id, channel="call",
            outcome=str(outcome or "").upper(), confidence="OBSERVED",
            metadata_json=extra or None, idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("emit_call_outcome failed: %s", exc)
        return None


def emit_reply_received(db: Session, *, lead_id: int, intent: str | None = None,
                        idempotency_key: str | None = None, **extra) -> OutreachEvent | None:
    try:
        return record_event(
            db, event_type="EMAIL_REPLY_RECEIVED", lead_id=lead_id, channel="email",
            outcome=str(intent or "").upper() or None, response_status="REPLIED",
            confidence="OBSERVED", metadata_json=extra or None, idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("emit_reply_received failed: %s", exc)
        return None
