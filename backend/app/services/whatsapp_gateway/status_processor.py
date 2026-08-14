"""
AiSensy delivery/read/failed status normaliser.

IMPORTANT: The exact AiSensy webhook payload schema is still UNKNOWN.
This module deliberately does not invent field names. It provides:

1. A normalised internal status vocabulary (DELIVERED / READ / FAILED / UNKNOWN)
2. A best-effort heuristic parser that looks for common Meta Cloud API shapes
3. Safe retention of the raw payload for later mapping once a real sample exists

When a real payload is obtained, update `_extract_fields` only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("whatsapp_gateway.status")


@dataclass
class NormalizedStatus:
    status: str  # DELIVERED | READ | FAILED | UNKNOWN
    message_id: str | None = None
    recipient_id: str | None = None
    timestamp: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    parse_notes: str = ""


class StatusNormalizer:
    """
    Convert an arbitrary AiSensy (or Meta-shaped) status payload into
    NormalizedStatus. Never raises on unknown shapes — returns UNKNOWN.
    """

    def normalise(self, payload: Any) -> NormalizedStatus:
        if payload is None:
            return NormalizedStatus(status="UNKNOWN", parse_notes="empty payload")

        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = payload.decode("utf-8", errors="replace")
            except Exception:
                return NormalizedStatus(status="UNKNOWN", parse_notes="undecodable bytes")

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return NormalizedStatus(
                    status="UNKNOWN",
                    parse_notes="payload is not valid JSON",
                    raw={"raw_text": payload[:2000]},
                )

        if not isinstance(payload, dict):
            return NormalizedStatus(
                status="UNKNOWN",
                parse_notes=f"payload type {type(payload).__name__} not a dict",
            )

        status, message_id, recipient_id, timestamp, notes = self._extract_fields(payload)
        return NormalizedStatus(
            status=status,
            message_id=message_id,
            recipient_id=recipient_id,
            timestamp=timestamp,
            raw=payload,
            parse_notes=notes,
        )

    def _extract_fields(
        self, payload: dict[str, Any]
    ) -> tuple[str, str | None, str | None, str | None, str]:
        """
        Best-effort extraction using shapes commonly seen in Meta Cloud API
        status webhooks and generic BSP forwards.

        This is NOT a verified AiSensy schema. Update when a real sample arrives.
        """
        notes: list[str] = []

        # Meta Cloud API style: { "statuses": [ { "id", "status", "timestamp", "recipient_id" } ] }
        statuses = payload.get("statuses")
        if isinstance(statuses, list) and statuses:
            first = statuses[0] if isinstance(statuses[0], dict) else {}
            st = str(first.get("status") or "").strip().lower()
            mid = str(first.get("id") or first.get("message_id") or "").strip() or None
            rid = str(first.get("recipient_id") or "").strip() or None
            ts = str(first.get("timestamp") or "").strip() or None
            return self._map_status(st), mid, rid, ts, "meta_cloud_api_statuses_shape"

        # Flat / alternative keys often used by BSPs
        for status_key in ("status", "event", "eventType", "event_type", "type"):
            if status_key in payload:
                st = str(payload.get(status_key) or "").strip().lower()
                mid = None
                for id_key in ("messageId", "message_id", "id", "wamid", "messageID"):
                    if payload.get(id_key):
                        mid = str(payload[id_key]).strip()
                        break
                rid = None
                for r_key in ("recipient_id", "destination", "phone", "to"):
                    if payload.get(r_key):
                        rid = str(payload[r_key]).strip()
                        break
                ts = str(payload.get("timestamp") or payload.get("time") or "").strip() or None
                notes.append(f"flat_key:{status_key}")
                return self._map_status(st), mid, rid, ts, ",".join(notes)

        notes.append("no_recognised_status_field")
        return "UNKNOWN", None, None, None, ",".join(notes)

    @staticmethod
    def _map_status(raw: str) -> str:
        r = (raw or "").lower()
        if r in ("delivered", "delivery", "delivery_confirmed"):
            return "DELIVERED"
        if r in ("read", "seen", "read_confirmed"):
            return "READ"
        if r in ("failed", "failure", "error", "undelivered", "rejected"):
            return "FAILED"
        if r in ("sent", "accepted", "queued"):
            # Sent/accepted is provider-side, not recipient delivery.
            # Treat as UNKNOWN for delivery purposes; ledger already has PROVIDER_ACCEPTED.
            return "UNKNOWN"
        return "UNKNOWN"
