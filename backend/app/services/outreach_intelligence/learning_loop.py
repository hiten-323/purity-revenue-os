"""Learning loop: event -> classify -> aggregates. Never touches kill switches/eligibility/DND."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.outreach_intelligence.models import (
    OutreachEvent, OutreachExperienceAggregate, OutreachOutcomeCorrection,
)
from app.services.outreach_intelligence.outcomes import (
    classify_email_reply, extract_call_commercial_signals,
)

log = logging.getLogger(__name__)
try:
    from app.services.outreach_learning import MIN_EVIDENCE
except Exception:  # noqa: BLE001
    MIN_EVIDENCE = 5

_POS_EMAIL = {"POSITIVE", "PRICE_REQUEST", "CATALOGUE_REQUEST", "SAMPLE_REQUEST", "CALLBACK_REQUEST"}
_POS_CALL = {
    "INTERESTED", "SEND_DETAILS", "SEND_PRICING", "SAMPLE_REQUESTED",
    "MEETING_REQUESTED", "CALLBACK", "PRICE_OBJECTION", "EXISTING_SUPPLIER",
    # call_intelligence result vocabulary + founder_call_pipeline FSM keys
    "CALLBACK_REQUESTED", "CATALOGUE_REQUESTED", "WHATSAPP_OPT_IN",
    "SEND_INFO_EMAIL", "HUMAN_HANDOFF",
}


def _lead_segment(db: Session, lead_id: int | None) -> tuple[str, str | None]:
    if not lead_id:
        return "UNKNOWN", None
    try:
        from app.models.models import B2BLead
        from app.services.smart_outreach import OutreachProfile
        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            return "UNKNOWN", None
        geo = getattr(lead, "city", None)
        prof = db.query(OutreachProfile).filter(OutreachProfile.lead_id == lead_id).first()
        if prof and getattr(prof, "category", None):
            return str(prof.category).upper(), geo
        return str(getattr(lead, "division", None) or "UNKNOWN").upper(), geo
    except Exception:  # noqa: BLE001
        return "UNKNOWN", None


def _upsert_aggregate(db: Session, *, segment_key: str, channel: str, metric: str, win: bool) -> None:
    row = (
        db.query(OutreachExperienceAggregate)
        .filter(
            OutreachExperienceAggregate.segment_key == segment_key,
            OutreachExperienceAggregate.channel == channel,
            OutreachExperienceAggregate.metric == metric,
        )
        .first()
    )
    if row is None:
        row = OutreachExperienceAggregate(
            segment_key=segment_key, channel=channel, metric=metric,
            value=0.0, sample_size=0, wins=0,
        )
        db.add(row)
    row.sample_size = int(row.sample_size or 0) + 1
    if win:
        row.wins = int(row.wins or 0) + 1
    row.value = (row.wins / row.sample_size) if row.sample_size else 0.0
    row.updated_at = datetime.utcnow()


def on_outreach_event(db: Session, event: OutreachEvent | dict) -> dict[str, Any]:
    if isinstance(event, dict):
        from app.services.outreach_intelligence.event_ledger import record_event
        event = record_event(db, **event)

    result: dict[str, Any] = {
        "event_id": getattr(event, "id", None),
        "updated_aggregates": False,
        "classification": None,
    }
    confidence = (getattr(event, "confidence", None) or "OBSERVED").upper()
    channel = (getattr(event, "channel", None) or "").lower()
    outcome = getattr(event, "outcome", None)
    event_type = getattr(event, "event_type", None) or ""

    classification = None
    if channel == "email" or "EMAIL" in event_type.upper() or "REPLY" in event_type.upper():
        classification = classify_email_reply(intent=outcome, event_type=event_type)
    elif channel == "call" or "CALL" in event_type.upper():
        meta = getattr(event, "metadata_json", None) or {}
        classification = extract_call_commercial_signals(
            outcome=outcome,
            transcript=(meta.get("transcript") if isinstance(meta, dict) else None),
            summary=(meta.get("summary") if isinstance(meta, dict) else None),
            payload=meta if isinstance(meta, dict) else {},
        )
    result["classification"] = classification

    # OBSERVED only
    if confidence and confidence != "OBSERVED":
        return result

    category, geo = _lead_segment(db, getattr(event, "lead_id", None))
    segment_key = f"{category}|{(geo or '').strip().upper()}" if geo else category
    win = False
    if channel == "email" and classification:
        win = classification.get("class") in _POS_EMAIL
        _upsert_aggregate(db, segment_key=segment_key, channel="email", metric="positive_reply_rate", win=win)
        _upsert_aggregate(db, segment_key=segment_key, channel="email", metric="reply_rate", win=True)
    elif channel == "call" and classification:
        sig = classification.get("signals") or {}
        win = bool(
            sig.get("interested") or sig.get("sample_requested") or sig.get("catalogue_requested")
            or sig.get("price_discussed") or sig.get("callback_requested") or sig.get("meeting_requested")
            or (outcome or "").upper() in _POS_CALL
        )
        _upsert_aggregate(db, segment_key=segment_key, channel="call", metric="positive_outcome_rate", win=win)

    # Mirror into LearnedPattern when strong enough
    try:
        from app.models.models import LearnedPattern
        for metric_name, ch in (("positive_reply_rate", "email"), ("reply_rate", "email"), ("positive_outcome_rate", "call")):
            agg = (
                db.query(OutreachExperienceAggregate)
                .filter(
                    OutreachExperienceAggregate.segment_key == segment_key,
                    OutreachExperienceAggregate.channel == ch,
                    OutreachExperienceAggregate.metric == metric_name,
                )
                .first()
            )
            if not agg or (agg.sample_size or 0) < MIN_EVIDENCE:
                continue
            scope, key = "category_channel", f"{category}:{ch}"
            row = (
                db.query(LearnedPattern)
                .filter(LearnedPattern.scope == scope, LearnedPattern.key == key, LearnedPattern.metric == metric_name)
                .first()
            )
            if row is None:
                row = LearnedPattern(scope=scope, key=key, metric=metric_name)
                db.add(row)
            row.value = float(agg.value or 0) * 100.0
            row.sample_size = int(agg.sample_size or 0)
            row.wins = int(agg.wins or 0)
            row.updated_at = datetime.utcnow()
    except Exception as exc:  # noqa: BLE001
        log.warning("LearnedPattern mirror skipped: %s", exc)

    result["updated_aggregates"] = True
    result["segment_key"] = segment_key
    result["win"] = win
    db.flush()
    return result


def recalculate_segment_stats(db: Session, category: str | None = None) -> dict[str, Any]:
    q = db.query(OutreachEvent).filter(
        (OutreachEvent.confidence == "OBSERVED") | (OutreachEvent.confidence.is_(None))
    )
    events = q.order_by(OutreachEvent.id.asc()).all()
    if category:
        db.query(OutreachExperienceAggregate).filter(
            OutreachExperienceAggregate.segment_key.like(f"{category.upper()}%")
        ).delete(synchronize_session=False)
    else:
        db.query(OutreachExperienceAggregate).delete(synchronize_session=False)
    db.flush()

    processed = 0
    for ev in events:
        cat, _geo = _lead_segment(db, ev.lead_id)
        if category and cat != category.upper():
            continue
        on_outreach_event(db, ev)
        processed += 1
    db.commit()
    return {
        "processed_events": processed,
        "category": category,
        "min_evidence": MIN_EVIDENCE,
        "note": "dry recalculation only — no outreach sent",
    }


def apply_correction(
    db: Session,
    *,
    lead_id: int,
    field: str,
    new_value: str,
    old_value: str | None = None,
    event_id: int | None = None,
    note: str | None = None,
    corrected_by: str = "FOUNDER",
) -> OutreachOutcomeCorrection:
    row = OutreachOutcomeCorrection(
        lead_id=lead_id, event_id=event_id, field=field,
        old_value=old_value, new_value=new_value, note=note,
        corrected_by=corrected_by, corrected_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row
