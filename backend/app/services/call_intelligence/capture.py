"""Open and finalize CallResult rows; guaranteed call_status writeback.

open_call_attempt()  -- at dial time (after the provider accepted the dispatch)
finalize_call()      -- on EVERY termination path; idempotent per call_id.

finalize_call never raises into the caller's outcome path: the pipeline's
state machine stays the authority for outreach_stage, this module only makes
sure (a) lead.call_status leaves CALLING, (b) the open CallHistory ledger rows
are concluded, and (c) a structured result + ledger event exist.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import inspect

from app.services.call_intelligence.classifier import classify_call, plain_summary
from app.services.call_intelligence.models import CallResult, ensure_for_session
from app.services.call_intelligence.taxonomy import (
    FOLLOW_UP_OUTCOMES, STOP_OUTCOMES, hour_bucket, terminal_call_status_for,
)

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

# Precedence when two sources disagree about the same call: a real report
# from the agent/engine always beats the reconciler's UNKNOWN_NO_RESULT, and a
# founder correction beats everything.
_SOURCE_RANK = {"reconciler": 0, "engine": 1, "voice_agent": 2, "founder": 3}


def new_call_ref(lead_id: int | None) -> str:
    return f"ppp-{lead_id or 0}-{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.utcnow()


def _parse_dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    try:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:  # noqa: BLE001
        try:
            return datetime.utcfromtimestamp(float(value) / (1000.0 if float(value) > 1e11 else 1.0))
        except Exception:  # noqa: BLE001
            return None


def _segment_of(lead) -> tuple[str | None, str | None]:
    bt = (getattr(lead, "segment", None) or getattr(lead, "division", None) or "").strip().lower() or None
    city = (getattr(lead, "city", None) or "").strip().title() or None
    return bt, city


def _local_hour(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(IST).hour


def _has_table(db, name: str) -> bool:
    try:
        return bool(inspect(db.connection()).has_table(name))
    except Exception:  # noqa: BLE001
        return False


def find_result(db, call_id: str | None) -> CallResult | None:
    if not call_id or not ensure_for_session(db):
        return None
    return db.query(CallResult).filter(CallResult.call_id == str(call_id)).first()


def latest_open_result(db, lead_id: int) -> CallResult | None:
    if not ensure_for_session(db):
        return None
    return (db.query(CallResult)
            .filter(CallResult.lead_id == lead_id, CallResult.status == "OPEN")
            .order_by(CallResult.id.desc()).first())


def open_call_attempt(db, lead, *, call_ref: str | None = None, provider: str | None = None,
                      provider_call_id: str | None = None, opener_variant: str | None = None,
                      script_variant: str | None = None, brief: dict | None = None,
                      started_at: datetime | None = None) -> CallResult | None:
    """Record that a dial went out. Idempotent on call_ref."""
    if not ensure_for_session(db):
        return None
    call_ref = call_ref or new_call_ref(getattr(lead, "id", None))
    row = find_result(db, call_ref)
    if row is not None:
        return row
    bt, city = _segment_of(lead)
    started = started_at or _utcnow()
    row = CallResult(
        call_id=call_ref, lead_id=lead.id, provider=provider,
        provider_call_id=(str(provider_call_id) if provider_call_id else None),
        attempt_number=(getattr(lead, "ai_call_count", 0) or 0) + 1,
        status="OPEN", started_at=started, opener_variant=opener_variant,
        script_variant=script_variant, business_type=bt, city=city,
        hour_bucket=hour_bucket(_local_hour(started)),
        brief=_compact_brief(brief), source="dialer", confidence=None,
    )
    db.add(row)
    db.flush()
    return row


def _compact_brief(brief: dict | None) -> dict | None:
    if not brief:
        return None
    keep = ("opener_variant", "script_variant", "avoid_openers", "handle_objections",
            "callback", "best_time_window", "prior_call_count", "segment_lessons_used",
            "skip", "prompt_block")
    return {k: brief.get(k) for k in keep if k in brief}


def _conclude_call_history(db, lead, *, fsm_key: str | None, connected: bool | None,
                           duration: float | None, transcript: str | None,
                           summary: str | None, reconciled: bool) -> int:
    """Close any CallHistory row still marked CALLING for this lead."""
    try:
        from app.models.models import CallHistory
    except Exception:  # noqa: BLE001
        return 0
    rows = (db.query(CallHistory)
            .filter(CallHistory.lead_id == lead.id, CallHistory.call_status == "CALLING")
            .order_by(CallHistory.id.desc()).all())
    for i, row in enumerate(rows):
        if reconciled:
            row.call_status = "RECONCILED"
            continue
        row.call_status = "COMPLETED" if connected else "ENDED"
        if i == 0:
            if fsm_key and (row.status or "") == "AI_CALL_ATTEMPTED":
                row.status = f"AI_{fsm_key}"
            if duration is not None and not row.duration:
                row.duration = float(duration)
            if transcript and not getattr(row, "call_transcript", None):
                row.call_transcript = transcript[:20000]
            if summary and not row.summary:
                row.summary = summary[:1000]
    return len(rows)


def _turns_to_text(turns) -> str:
    lines = []
    for t in turns or []:
        if isinstance(t, dict) and str(t.get("role")) in {"user", "assistant"}:
            lines.append(f"{t.get('role')}: {t.get('text') or ''}")
    return "\n".join(lines)


def _next_action(outcome: str, *, callback_window: str | None, lead,
                 now: datetime) -> tuple[str | None, datetime | None]:
    if outcome in STOP_OUTCOMES:
        return "STOP", None
    if outcome == "CALLBACK_REQUESTED":
        when = None
        if callback_window:
            try:
                from app.services.founder_call_pipeline import parse_callback_datetime
                when = parse_callback_datetime(callback_window, now)
            except Exception:  # noqa: BLE001
                when = None
        return "CALLBACK", when or getattr(lead, "ai_retry_after", None)
    if outcome == "SAMPLE_REQUESTED":
        return "FOUNDER_SEND_SAMPLE", None
    if outcome == "CATALOGUE_REQUESTED":
        return "SEND_CATALOGUE", None
    if outcome == "INTERESTED":
        return "FOUNDER_FOLLOW_UP", None
    if outcome in {"NO_ANSWER", "BUSY", "VOICEMAIL", "FAILED"}:
        return "RETRY_PER_POLICY", getattr(lead, "ai_retry_after", None)
    return "FOUNDER_REVIEW", None


def finalize_call(db, lead, *, fsm_key: str | None = None,
                  termination_reason: str | None = None,
                  source: str = "voice_agent",
                  meta: dict | None = None,
                  transcript: str | None = None,
                  turns: list | None = None,
                  summary: str | None = None,
                  details: dict | None = None,
                  callback_window: str | None = None,
                  sip_status_code: int | None = None,
                  now: datetime | None = None,
                  result_override: dict | None = None) -> CallResult | None:
    """Write the terminal state for this lead's current call. Idempotent.

    Always clears call_status=CALLING, even if the CallResult table cannot
    be written (the writeback is the guarantee; the record is the bonus).
    """
    meta = dict(meta or {})
    now = now or _utcnow()
    reconciled = source == "reconciler"
    turns = turns if turns is not None else meta.get("turns")
    if not transcript and turns:
        transcript = _turns_to_text(turns)

    cls = classify_call(
        fsm_key=fsm_key, termination_reason=termination_reason, turns=turns,
        transcript=transcript, summary=summary, details=details,
        language_hint=meta.get("language"),
        answered=bool(meta.get("answered_at")) or None,
        opener_variant=meta.get("opener_variant"),
    )
    if result_override:
        cls.update({k: v for k, v in result_override.items() if v is not None})

    # 1. The guarantee: this lead is no longer CALLING.
    try:
        lead.call_status = terminal_call_status_for(
            cls["outcome"], connected=cls["connected"], reconciled=reconciled)
    except Exception:  # noqa: BLE001
        lead.call_status = "UNKNOWN_NO_RESULT"
    started = _parse_dt(meta.get("started_at"))
    answered = _parse_dt(meta.get("answered_at"))
    ended = _parse_dt(meta.get("ended_at")) or (None if reconciled else now)
    duration = meta.get("duration_seconds")
    if duration is None and answered and ended:
        duration = max(0.0, (ended - answered).total_seconds())
    try:
        if duration is not None:
            lead.call_duration_seconds = float(duration)
        lead.human_answered = bool(cls["connected"]) if cls["connected"] is not None else lead.human_answered
    except Exception:  # noqa: BLE001
        pass
    try:
        _conclude_call_history(db, lead, fsm_key=(fsm_key or "").upper() or None,
                               connected=cls["connected"], duration=duration,
                               transcript=transcript, summary=summary,
                               reconciled=reconciled)
    except Exception as exc:  # noqa: BLE001
        log.warning("call_history conclude failed for lead %s: %s", getattr(lead, "id", None), exc)

    # 2. The structured record.
    if not ensure_for_session(db):
        return None
    call_id = meta.get("call_ref") or meta.get("call_id")
    row = find_result(db, call_id) if call_id else None
    if row is None:
        row = latest_open_result(db, lead.id)
        if row is not None and call_id and row.call_id != call_id:
            # A different attempt's report; only adopt the open row when the
            # caller could not identify its call.
            row = None
    if row is not None and row.status == "FINAL":
        old_rank = _SOURCE_RANK.get(row.source or "", 1)
        new_rank = _SOURCE_RANK.get(source, 1)
        if new_rank < old_rank or (new_rank == old_rank and row.outcome == cls["outcome"]):
            return row  # duplicate / lower-precedence report: no-op
    if row is None:
        bt, city = _segment_of(lead)
        fallback_id = call_id or (
            f"lead{lead.id}-a{getattr(lead, 'ai_call_count', 0) or 0}-"
            f"{(fsm_key or termination_reason or 'UNKNOWN').upper()}-{now:%Y%m%d%H%M%S}")
        existing = find_result(db, fallback_id)
        if existing is not None:
            return existing
        row = CallResult(call_id=str(fallback_id)[:96], lead_id=lead.id,
                         status="OPEN", business_type=bt, city=city,
                         started_at=started or getattr(lead, "last_call_date", None),
                         attempt_number=getattr(lead, "ai_call_count", None),
                         opener_variant=meta.get("opener_variant"),
                         script_variant=meta.get("script_variant"))
        row.hour_bucket = hour_bucket(_local_hour(row.started_at))
        db.add(row)

    row.status = "FINAL"
    row.source = source
    row.provider = row.provider or meta.get("provider")
    row.provider_call_id = row.provider_call_id or meta.get("provider_call_id")
    row.room_name = meta.get("room_name") or row.room_name
    row.sip_call_id = meta.get("sip_call_id") or row.sip_call_id
    row.started_at = row.started_at or started
    row.answered_at = answered or row.answered_at
    row.ended_at = ended or row.ended_at
    row.duration_seconds = float(duration) if duration is not None else row.duration_seconds
    row.sip_status_code = sip_status_code if sip_status_code is not None else row.sip_status_code
    row.opener_variant = row.opener_variant or meta.get("opener_variant")
    row.script_variant = row.script_variant or meta.get("script_variant")
    for f in ("outcome", "raw_outcome", "termination_reason", "connected",
              "reached_decision_maker", "objections", "drop_off_turn",
              "drop_off_stage", "turn_count", "user_turn_count", "language",
              "sentiment", "rude_or_complaint", "lessons", "evidence", "confidence"):
        setattr(row, f, cls.get(f))
    na, na_at = _next_action(cls["outcome"], callback_window=callback_window, lead=lead, now=now)
    row.next_action, row.next_action_at = na, na_at
    row.summary = plain_summary(cls, summary)
    row.updated_at = now
    db.flush()

    # 3. Learning feed (advisory; failures never undo the writeback).
    try:
        feed_ledger(db, row, lead)
    except Exception as exc:  # noqa: BLE001
        log.warning("call result ledger feed failed for lead %s: %s", lead.id, exc)
    return row


def feed_ledger(db, row: CallResult, lead=None, *, update_aggregates: bool = True) -> None:
    """Mirror a FINAL CallResult into the PR #33 event ledger + aggregates."""
    if not _has_table(db, "outreach_events"):
        return
    from app.services.outreach_intelligence.event_ledger import record_event
    from app.services.outreach_intelligence.models import OutreachEvent
    conf = row.confidence or "LOW_CONFIDENCE"
    ledger_conf = "OBSERVED" if conf in {"OBSERVED", "HUMAN_CORRECTED"} else (
        "INFERRED" if conf == "INFERRED" else "LOW_CONFIDENCE")
    idem = f"call_result:{row.id}:{row.outcome}:{ledger_conf}"
    if db.query(OutreachEvent.id).filter(OutreachEvent.idempotency_key == idem).first():
        return  # already mirrored; never double-count an aggregate
    ev = record_event(
        db,
        event_type="CALL_RESULT_RECONCILED" if row.source == "reconciler" else "CALL_RESULT_CAPTURED",
        lead_id=row.lead_id, company=getattr(lead, "company", None), channel="call",
        occurred_at=row.ended_at or row.updated_at, outcome=row.outcome,
        confidence=ledger_conf, message_version=row.opener_variant,
        template_version_id=row.script_variant,
        next_action=row.next_action,
        metadata_json={
            "source": "call_results", "call_result_id": row.id, "call_id": row.call_id,
            "connected": row.connected, "reached_decision_maker": row.reached_decision_maker,
            "objections": row.objections, "drop_off_stage": row.drop_off_stage,
            "drop_off_turn": row.drop_off_turn, "language": row.language,
            "business_type": row.business_type, "city": row.city,
            "hour_bucket": row.hour_bucket, "summary": row.summary,
            "raw_outcome": row.raw_outcome,
        },
        idempotency_key=idem,
    )
    # Reconciled rows are never fed into aggregates (they were either already
    # mirrored from call_outcome_last, or they are "we don't know").
    if (update_aggregates and ledger_conf == "OBSERVED" and row.source != "reconciler"
            and _has_table(db, "outreach_experience_aggregates")):
        from app.services.outreach_intelligence.learning_loop import on_outreach_event
        on_outreach_event(db, ev)


def follow_up_needed(row: CallResult) -> bool:
    return (row.outcome or "") in FOLLOW_UP_OUTCOMES
