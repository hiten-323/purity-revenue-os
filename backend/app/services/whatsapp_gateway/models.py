"""
Persistence models for the WhatsApp Gateway idempotency ledger.

Uses the repository's existing SQLAlchemy Base and SQLite conventions.
Does not introduce a second database.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.database.database import Base


class WhatsAppSendLedger(Base):
    """
    One row per logical WhatsApp send attempt originating from Klaviyo.

    Idempotency key = profile_id + lifecycle_stage + source_event_id
    Unique constraint on idempotency_key prevents duplicate provider calls
    even under concurrent requests.
    """

    __tablename__ = "whatsapp_send_ledger"

    id = Column(Integer, primary_key=True, index=True)

    # Identity of the logical event
    profile_id = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=True)  # normalised MSISDN; may be empty if blocked early
    lifecycle_stage = Column(String, nullable=False, index=True)  # ATC | CHECKOUT | POST_PURCHASE | …
    campaign_name = Column(String, nullable=True)
    source_event_id = Column(String, nullable=False, index=True)
    order_id = Column(String, nullable=True, index=True)

    # Deterministic key: profile_id|lifecycle_stage|source_event_id
    idempotency_key = Column(String, nullable=False, unique=True, index=True)

    # Provider correlation
    aisensy_message_id = Column(String, nullable=True, index=True)  # wamid when known

    # Lifecycle of this send
    # PENDING | BLOCKED_CONSENT | BLOCKED_PHONE | PROVIDER_ACCEPTED | DELIVERED | READ | FAILED | ERROR
    status = Column(String, nullable=False, default="PENDING", index=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)

    # Optional raw status payload retained for schema discovery (never contains API key)
    last_status_payload = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_wa_ledger_stage_status", "lifecycle_stage", "status"),
    )


# Internal request model (not a DB table)
class WhatsAppSendRequest:
    """
    Normalised internal representation of a Klaviyo → Gateway send request.

    Klaviyo webhook payload shape is not yet locked; this class is the stable
    boundary the rest of the gateway depends on.
    """

    __slots__ = (
        "profile_id",
        "phone",
        "lifecycle_stage",
        "campaign_name",
        "source_event_id",
        "order_id",
        "user_name",
        "template_params",
        "whatsapp_marketing_consent",
        "raw",
    )

    def __init__(
        self,
        *,
        profile_id: str,
        phone: str | None,
        lifecycle_stage: str,
        source_event_id: str,
        campaign_name: str | None = None,
        order_id: str | None = None,
        user_name: str | None = None,
        template_params: list[str] | None = None,
        whatsapp_marketing_consent: str | None = None,
        raw: dict | None = None,
    ):
        self.profile_id = (profile_id or "").strip()
        self.phone = phone
        self.lifecycle_stage = (lifecycle_stage or "").strip().upper()
        self.campaign_name = campaign_name
        self.source_event_id = (source_event_id or "").strip()
        self.order_id = order_id
        self.user_name = user_name or "there"
        self.template_params = template_params or []
        # Expected values: SUBSCRIBED | UNSUBSCRIBED | NEVER_SUBSCRIBED | None
        self.whatsapp_marketing_consent = (
            (whatsapp_marketing_consent or "").strip().upper() or None
        )
        self.raw = raw or {}

    @property
    def idempotency_key(self) -> str:
        return f"{self.profile_id}|{self.lifecycle_stage}|{self.source_event_id}"

    def validate_required(self) -> list[str]:
        errors = []
        if not self.profile_id:
            errors.append("profile_id is required")
        if not self.lifecycle_stage:
            errors.append("lifecycle_stage is required")
        if not self.source_event_id:
            errors.append("source_event_id is required")
        return errors
