"""
Persistent idempotency ledger for Klaviyo → AiSensy sends.

Key: profile_id + lifecycle_stage + source_event_id

Uses the repository's existing SQLAlchemy session pattern.
Unique constraint on idempotency_key makes concurrent duplicates safe.

On SQLite, writers are further serialized with BEGIN IMMEDIATE so the
check-then-insert path cannot invent multiple is_new=True winners under
thread contention.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.services.whatsapp_gateway.models import WhatsAppSendLedger

logger = logging.getLogger("whatsapp_gateway.idempotency")

TERMINAL_SENT_STATUSES = {
    "PROVIDER_ACCEPTED",
    "DELIVERED",
    "READ",
}

# How many times to re-read after losing a unique-key race.
_CLAIM_RETRIES = 3


class IdempotencyLedger:
    def __init__(self, db: Session):
        self.db = db

    def get_by_key(self, idempotency_key: str) -> Optional[WhatsAppSendLedger]:
        return (
            self.db.query(WhatsAppSendLedger)
            .filter(WhatsAppSendLedger.idempotency_key == idempotency_key)
            .first()
        )

    def _begin_immediate(self) -> None:
        """
        Take a reserved write lock on SQLite before check+insert.

        Without this, concurrent sessions can all pass get_by_key() before any
        commit, rely solely on UNIQUE, and then hit connection/session state
        races on IntegrityError recovery. BEGIN IMMEDIATE serializes writers.
        On non-SQLite backends this is a no-op if the dialect rejects it.
        """
        bind = self.db.get_bind()
        if bind is None or bind.dialect.name != "sqlite":
            return
        try:
            self.db.execute(text("BEGIN IMMEDIATE"))
        except OperationalError:
            # Already inside a transaction — keep going under current lock.
            pass

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

        Under concurrent identical keys, exactly one caller gets is_new=True.
        """
        key = f"{profile_id}|{lifecycle_stage}|{source_event_id}"

        last_err: Exception | None = None
        for attempt in range(_CLAIM_RETRIES):
            try:
                self._begin_immediate()

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
                self.db.commit()
                self.db.refresh(row)
                return row, True

            except IntegrityError as exc:
                # Lost the unique-key race. Drop failed state and re-read winner.
                last_err = exc
                self.db.rollback()
                self.db.expire_all()
                existing = self.get_by_key(key)
                if existing is not None:
                    return existing, False
                # Winner not visible yet — retry under a fresh IMMEDIATE lock.
                logger.debug(
                    "idempotency claim retry %s/%s for key=%s",
                    attempt + 1,
                    _CLAIM_RETRIES,
                    key,
                )
                continue

            except OperationalError as exc:
                # busy/locked — rollback and retry
                last_err = exc
                try:
                    self.db.rollback()
                except Exception:
                    pass
                self.db.expire_all()
                existing = self.get_by_key(key)
                if existing is not None:
                    return existing, False
                continue

        # Final visibility check before failing closed.
        existing = self.get_by_key(key)
        if existing is not None:
            return existing, False
        raise RuntimeError(
            f"idempotency claim failed for key={key!r} after {_CLAIM_RETRIES} attempts"
        ) from last_err

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
