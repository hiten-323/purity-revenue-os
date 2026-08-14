"""
Persistent idempotency ledger for Klaviyo → AiSensy sends.

Key: profile_id + lifecycle_stage + source_event_id

Uses the repository's existing SQLAlchemy session pattern.
Unique constraint on idempotency_key makes concurrent duplicates safe.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.whatsapp_gateway.models import WhatsAppSendLedger

logger = logging.getLogger("whatsapp_gateway.idempotency")

TERMINAL_SENT_STATUSES = {
    "PROVIDER_ACCEPTED",
    "DELIVERED",
    "READ",
}


class IdempotencyLedger:
    def __init__(self, db: Session):
        self.db = db

    def get_by_key(self, idempotency_key: str) -> Optional[WhatsAppSendLedger]:
        return (
            self.db.query(WhatsAppSendLedger)
            .filter(WhatsAppSendLedger.idempotency_key == idempotency_key)
            .first()
        )

    def begin_send(
        self,
        *,
        profile_id: str,
        phone: str | None,
        lifecycle_stage: str,
        source_event_id: str,
        campaign_name: str | None = None,
        order_id: str | None = None,
        initial_status: str = "PENDING",
    ) -> tuple[WhatsAppSendLedger, bool]:
        """
        Atomically claim the right to send for this logical event.

        Returns:
            (row, is_new)
            is_new=False means a prior row already exists — caller must NOT send again
            if that row is already in a sent/terminal state.
        """
        key = f"{profile_id}|{lifecycle_stage}|{source_event_id}"
        existing = self.get_by_key(key)
        if existing is not None:
            return existing, False

        row = WhatsAppSendLedger(
            profile_id=profile_id,
            phone=phone,
            lifecycle_stage=lifecycle_stage,
            campaign_name=campaign_name,
            source_event_id=source_event_id,
            order_id=order_id,
            idempotency_key=key,
            status=initial_status,
            created_at=datetime.utcnow(),
        )
        self.db.add(row)
        try:
            self.db.commit()
            self.db.refresh(row)
            return row, True
        except IntegrityError:
            # Concurrent insert won the race.
            self.db.rollback()
            existing = self.get_by_key(key)
            if existing is None:
                raise
            return existing, False

    def mark_blocked(self, row: WhatsAppSendLedger, status: str, error: str) -> WhatsAppSendLedger:
        row.status = status
        row.error = (error or "")[:2000]
        self.db.commit()
        self.db.refresh(row)
        return row

    def mark_provider_accepted(
        self,
        row: WhatsAppSendLedger,
        *,
        message_id: str = "",
        campaign_name: str | None = None,
    ) -> WhatsAppSendLedger:
        row.status = "PROVIDER_ACCEPTED"
        row.aisensy_message_id = (message_id or "")[:255] or None
        row.sent_at = datetime.utcnow()
        if campaign_name:
            row.campaign_name = campaign_name
        row.error = None
        self.db.commit()
        self.db.refresh(row)
        return row

    def mark_failed(self, row: WhatsAppSendLedger, error: str) -> WhatsAppSendLedger:
        row.status = "FAILED"
        row.failed_at = datetime.utcnow()
        row.error = (error or "")[:2000]
        self.db.commit()
        self.db.refresh(row)
        return row

    def apply_status(
        self,
        *,
        message_id: str | None = None,
        idempotency_key: str | None = None,
        status: str,
        raw_payload: str | None = None,
    ) -> Optional[WhatsAppSendLedger]:
        """
        Update ledger from a normalised status event.

        Looks up by aisensy_message_id first, then by idempotency_key.
        """
        row = None
        if message_id:
            row = (
                self.db.query(WhatsAppSendLedger)
                .filter(WhatsAppSendLedger.aisensy_message_id == message_id)
                .first()
            )
        if row is None and idempotency_key:
            row = self.get_by_key(idempotency_key)
        if row is None:
            return None

        now = datetime.utcnow()
        if status == "DELIVERED":
            row.status = "DELIVERED"
            row.delivered_at = row.delivered_at or now
        elif status == "READ":
            row.status = "READ"
            row.read_at = row.read_at or now
            if not row.delivered_at:
                row.delivered_at = now
        elif status == "FAILED":
            row.status = "FAILED"
            row.failed_at = row.failed_at or now
        # UNKNOWN leaves existing status alone but still stores payload for discovery

        if raw_payload is not None:
            row.last_status_payload = raw_payload[:8000]

        self.db.commit()
        self.db.refresh(row)
        return row

    @staticmethod
    def already_sent(row: WhatsAppSendLedger) -> bool:
        return row.status in TERMINAL_SENT_STATUSES
