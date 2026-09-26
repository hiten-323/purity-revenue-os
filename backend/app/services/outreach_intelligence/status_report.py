"""Read-only live Auto-Outreach status aggregation."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.services.outreach_intelligence.models import OutreachEvent


POSITIVE = {
    "INTERESTED", "POSITIVE", "SAMPLE_REQUESTED", "CATALOGUE_REQUESTED",
    "PRICING_REQUESTED", "MEETING_REQUESTED", "CALLBACK",
}
CALLBACKS = {"CALLBACK", "CALL_LATER", "MEETING_REQUESTED"}
CONSENTS = {"WHATSAPP_OPT_IN", "WHATSAPP_CONSENT", "CONSENT_GRANTED"}
FAILURES = {"FAILED", "BLOCKED", "NOT_CONFIGURED", "NO_ANSWER", "BUSY"}


def _bucket() -> dict[str, int]:
    return {
        "attempted": 0,
        "sent": 0,
        "delivered": 0,
        "replies": 0,
        "positive": 0,
        "commercial": 0,
        "callbacks_or_meetings": 0,
        "consents": 0,
        "failures": 0,
        "bounces": 0,
        "opt_outs": 0,
    }


def _classify(e: OutreachEvent) -> tuple[str, str, str, str]:
    channel = (e.channel or "").lower()
    event_type = (e.event_type or "").upper()
    delivery = (e.delivery_status or "").upper()
    response = (e.response_status or "").upper()
    outcome = (e.outcome or "").upper()
    if channel not in {"email", "whatsapp", "call"}:
        if "CALL" in event_type:
            channel = "call"
        elif "WHATSAPP" in event_type:
            channel = "whatsapp"
        elif "EMAIL" in event_type or "TOUCH_" in event_type:
            channel = "email"
    return channel, event_type, delivery, response or outcome


def _apply(bucket: dict[str, int], e: OutreachEvent) -> None:
    channel, event_type, delivery, response_or_outcome = _classify(e)
    outcome = (e.outcome or "").upper()
    attempted = (
        event_type.startswith("TOUCH_")
        or "SENT" in event_type
        or event_type in {"CALL_OUTCOME_RECORDED", "CALL_ATTEMPTED", "CALL_PLACED"}
    )
    if attempted:
        bucket["attempted"] += 1
    if channel in {"email", "whatsapp"}:
        if delivery in {"SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ"} or event_type.endswith("SENT"):
            bucket["sent"] += 1
        if delivery in {"DELIVERED", "READ"}:
            bucket["delivered"] += 1
        if response_or_outcome == "REPLIED" or "REPLY" in event_type:
            bucket["replies"] += 1
    if outcome in POSITIVE:
        bucket["positive"] += 1
    if outcome in {"PRICING_REQUESTED", "CATALOGUE_REQUESTED", "SAMPLE_REQUESTED"}:
        bucket["commercial"] += 1
    if outcome in CALLBACKS:
        bucket["callbacks_or_meetings"] += 1
    if outcome in CONSENTS or "WHATSAPP_OPT_IN" in event_type:
        bucket["consents"] += 1
    if outcome in FAILURES or "FAILED" in event_type or "BLOCKED" in event_type:
        bucket["failures"] += 1
    if outcome in {"BOUNCE", "HARD_BOUNCE"} or "BOUNCE" in event_type:
        bucket["bounces"] += 1
    if outcome in {"UNSUBSCRIBE", "DO_NOT_CONTACT", "OPTED_OUT"}:
        bucket["opt_outs"] += 1


def build_status_report(db: Session, hours: int = 3) -> dict[str, Any]:
    hours = max(1, min(int(hours), 168))
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)

    events = (
        db.query(OutreachEvent)
        .filter(
            OutreachEvent.occurred_at >= start,
            OutreachEvent.occurred_at < end,
        )
        .order_by(OutreachEvent.occurred_at.desc(), OutreachEvent.id.desc())
        .all()
    )

    email = _bucket()
    whatsapp = _bucket()
    calls = _bucket()
    failures: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []

    for event in events:
        channel, event_type, delivery, response_or_outcome = _classify(event)
        target = calls if channel == "call" else whatsapp if channel == "whatsapp" else email
        _apply(target, event)

        outcome = (event.outcome or "").upper()
        if outcome in FAILURES or "FAILED" in event_type or "BLOCKED" in event_type:
            failures.append({
                "event_id": event.id,
                "lead_id": event.lead_id,
                "channel": channel,
                "event_type": event.event_type,
                "delivery_status": event.delivery_status,
                "provider_status": event.provider_status,
                "outcome": event.outcome,
                "at": event.occurred_at.isoformat() if event.occurred_at else None,
            })
        if outcome in POSITIVE | CONSENTS | CALLBACKS:
            attention.append({
                "event_id": event.id,
                "lead_id": event.lead_id,
                "channel": channel,
                "event_type": event.event_type,
                "outcome": event.outcome,
                "next_action": event.next_action,
                "at": event.occurred_at.isoformat() if event.occurred_at else None,
            })

    return {
        "report": "auto_outreach_status",
        "window_hours": hours,
        "activity": bool(events),
        "activity_statement": (
            f"{len(events)} outreach events recorded in the last {hours} hours."
            if events else f"No outreach events were recorded in the last {hours} hours."
        ),
        "window_utc": {
            "start": start.isoformat(timespec="seconds") + "Z",
            "end": end.isoformat(timespec="seconds") + "Z",
        },
        "window_ist": {
            "start": (start + timedelta(hours=5, minutes=30)).isoformat(timespec="seconds"),
            "end": (end + timedelta(hours=5, minutes=30)).isoformat(timespec="seconds"),
        },
        "totals": {
            "events": len(events),
            "attempted": email["attempted"] + whatsapp["attempted"] + calls["attempted"],
            "delivered": email["delivered"] + whatsapp["delivered"] + calls["delivered"],
            "replies": email["replies"] + whatsapp["replies"],
            "cold_lead_responses": sum(1 for e in events if (e.outcome or "").upper() in POSITIVE),
            "whatsapp_consents": sum(1 for e in events if (e.outcome or "").upper() in CONSENTS or "WHATSAPP_OPT_IN" in (e.event_type or "").upper()),
            "callbacks_requested": sum(1 for e in events if (e.outcome or "").upper() in CALLBACKS),
            "failures": len(failures),
        },
        "channels": {
            "email": email,
            "whatsapp": whatsapp,
            "calls": calls,
        },
        "failures": failures,
        "attention": attention,
        "recent_events": [
            {
                "id": e.id,
                "lead_id": e.lead_id,
                "channel": e.channel,
                "event_type": e.event_type,
                "delivery_status": e.delivery_status,
                "response_status": e.response_status,
                "outcome": e.outcome,
                "next_action": e.next_action,
                "at": e.occurred_at.isoformat() if e.occurred_at else None,
            }
            for e in events[:100]
        ],
        "safety": {
            "read_only": True,
            "does_not_send": True,
            "does_not_dial": True,
            "does_not_enable_flags": True,
        },
    }
