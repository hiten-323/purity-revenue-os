"""Durable ledger for outreach email sends.

Automatic outreach used to transmit first and record later. ``execute_one``
held a SQLite write transaction open across SMTP (``classify_lead`` flushes
the profile before the send), then ``db.commit()`` on that same session.
``run_automatic_cycle`` rolls the session back when that commit raises
``database is locked`` or the process dies between accept and commit.

Cadence only counts ``WorkflowEvent`` rows of type ``EMAIL_SENT``. With the
row gone, the lead still looks never emailed: the next touch is the same
intro and it is immediately due. The worker is fit-ordered, so the same lead
is selected again on the next cycle. The SMTP chokepoint did not enforce
"this template was already sent".

This ledger closes that hole for every caller of ``send_email``:

  * Claim a row and commit it on its own short transaction BEFORE SMTP, so a
    later rollback of the caller's session cannot erase the attempt.
  * ``SENT`` and unresolved ``CLAIMED`` (crash after claim, outcome unknown)
    block another send of the same template/stage OR an identical body to the
    same lead or the same mailbox.
  * ``FAILED`` is not a send. It does not advance cadence and it does not
    block a later attempt. A failure must not look like success.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, inspect, or_, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.database.database import Base

CLAIMED = "CLAIMED"
SENT = "SENT"
FAILED = "FAILED"
BLOCKING = (SENT, CLAIMED)

# Later pipeline states must not be walked backwards to EMAIL_SENT.
_DO_NOT_DOWNGRADE = {
    "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT",
    "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED",
    "UPSELL_OFFERED", "ACCOUNT_GROWTH", "DO_NOT_CONTACT", "CLOSED_LOST",
    "DISQUALIFIED", "WHATSAPP_SENT", "OPTED_OUT", "BOUNCED",
}
_STAGE_INDEX = {"intro": 1, "nudge": 2, "proof": 3, "ask": 4, "breakup": 5}


class EmailSendLedger(Base):
    """One row per logical outreach email attempt.

    ``idempotency_key`` is the exact attempt (mailbox + stage + body). The
    refusal rule is wider than that key: any blocking row for the same person
    whose stage matches OR whose body matches is enough to refuse.
    """

    __tablename__ = "email_send_ledger"

    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    lead_id = Column(Integer, nullable=True, index=True)
    to_email = Column(String, nullable=False, index=True)
    stage = Column(String, nullable=True, index=True)
    body_hash = Column(String, nullable=False, index=True)
    subject = Column(String, nullable=True)
    status = Column(String, nullable=False, index=True)
    message_id = Column(String, nullable=True)
    error = Column(String, nullable=True)
    claimed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class SendClaim:
    def __init__(self, *, proceed: bool, key: str, reason: str = ""):
        self.proceed = proceed
        self.key = key
        self.reason = reason


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_body(value: str | None) -> str:
    text_value = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text_value.split("\n")).strip()


def body_hash(value: str | None) -> str:
    return hashlib.sha256(normalize_body(value).encode("utf-8")).hexdigest()


def _stage(value: str | None) -> str:
    return (value or "").strip().lower()


def _attempt_key(to_email: str, stage: str, digest: str) -> str:
    raw = f"{to_email}|{stage}|{digest}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _session() -> Session:
    from app.database.database import SessionLocal
    return SessionLocal()


def _ensure_table() -> None:
    db = _session()
    try:
        EmailSendLedger.__table__.create(bind=db.get_bind(), checkfirst=True)
        db.commit()
    finally:
        db.close()


def _begin_immediate(db: Session) -> None:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "sqlite":
        return
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except OperationalError:
        # Already inside a transaction — stay on the current lock.
        pass


def table_ready(db: Session) -> bool:
    """True when this process can read the ledger. A missing table is not an error.

    Uses a fresh connection so a missing-table probe cannot abort the
    caller's transaction.
    """
    try:
        bind = db.get_bind()
        if bind is None:
            return False
        if bind.dialect.name == "sqlite":
            with bind.connect() as conn:
                found = conn.execute(
                    text(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"
                    ),
                    {"name": EmailSendLedger.__tablename__},
                ).first()
            return found is not None
        return inspect(bind).has_table(EmailSendLedger.__tablename__)
    except Exception:
        return False


def _person_clause(lead_id: int | None, to_email: str):
    parts = []
    if to_email:
        parts.append(EmailSendLedger.to_email == to_email)
    if lead_id:
        parts.append(EmailSendLedger.lead_id == lead_id)
    if not parts:
        return None
    return or_(*parts)


def _blocking_rows(db: Session, *, lead_id: int | None, to_email: str) -> list[EmailSendLedger]:
    person = _person_clause(lead_id, to_email)
    if person is None:
        return []
    return (
        db.query(EmailSendLedger)
        .filter(EmailSendLedger.status.in_(BLOCKING), person)
        .all()
    )


def _reason_for(row: EmailSendLedger, stage: str, digest: str) -> str:
    same_stage = bool(stage) and (row.stage or "") == stage
    same_body = bool(digest) and row.body_hash == digest
    if row.status == CLAIMED:
        why = "same template/stage" if same_stage and not same_body else (
            "identical body" if same_body and not same_stage else
            "same template/stage or identical body"
        )
        return (
            "outreach email already in flight after an unresolved send "
            f"({why}); not sending it again"
        )
    if same_stage and not same_body:
        why = "same template/stage"
    elif same_body and not same_stage:
        why = "identical body"
    else:
        why = "same template/stage or identical body"
    return f"already sent this outreach email ({why})"


def _blocking_reason(
    db: Session,
    *,
    lead_id: int | None,
    to_email: str,
    stage: str,
    digest: str,
) -> str | None:
    for row in _blocking_rows(db, lead_id=lead_id, to_email=to_email):
        same_stage = bool(stage) and (row.stage or "") == stage
        same_body = bool(digest) and row.body_hash == digest
        if same_stage or same_body:
            return _reason_for(row, stage, digest)
    return None


def repeat_block_reason(
    db: Session,
    *,
    lead_id: int | None,
    to_email: str | None,
    stage: str | None,
    body: str | None = "",
) -> str | None:
    """Read-only eligibility check. None means this attempt is not a repeat.

    A missing ledger table returns None so databases created before this
    table existed keep their previous behaviour; ``send_email`` still creates
    the table and enforces the rule at the chokepoint.
    """
    if not table_ready(db):
        return None
    return _blocking_reason(
        db,
        lead_id=lead_id,
        to_email=normalize_email(to_email),
        stage=_stage(stage),
        digest=body_hash(body) if body else "",
    )


def sent_markers(db: Session, lead) -> list[tuple[datetime, str]]:
    """Proven sends for cadence. FAILED and CLAIMED do not count.

    CLAIMED is an unknown outcome: it blocks a resend of that stage, but it
    must not advance the sequence (that would schedule the next touch off a
    send we cannot prove).
    """
    if lead is None or not table_ready(db):
        return []
    person = _person_clause(getattr(lead, "id", None), normalize_email(getattr(lead, "email", "")))
    if person is None:
        return []
    rows = (
        db.query(EmailSendLedger)
        .filter(EmailSendLedger.status == SENT, person)
        .all()
    )
    out = []
    for row in rows:
        out.append((row.sent_at or row.claimed_at or datetime.min, row.message_id or ""))
    return out


def stage_occupied(db: Session, lead, stage: str | None) -> bool:
    """True when this exact template/stage is SENT or still CLAIMED."""
    stage_norm = _stage(stage)
    if lead is None or not stage_norm or not table_ready(db):
        return False
    rows = _blocking_rows(
        db,
        lead_id=getattr(lead, "id", None),
        to_email=normalize_email(getattr(lead, "email", "")),
    )
    return any((row.stage or "") == stage_norm for row in rows)


def begin_send(
    *,
    lead_id: int | None,
    to_email: str | None,
    stage: str | None,
    body: str | None,
    subject: str | None = "",
) -> SendClaim:
    """Commit a CLAIMED row, or refuse if this email was already sent.

    Raises if the ledger cannot be written. Callers must not SMTP in that
    case: sending without a durable claim is how a retry becomes a second copy.
    """
    email_norm = normalize_email(to_email)
    stage_norm = _stage(stage)
    digest = body_hash(body)
    key = _attempt_key(email_norm, stage_norm, digest)
    if not email_norm:
        return SendClaim(proceed=False, key=key, reason="missing recipient; email not sent")

    _ensure_table()
    last_error: Exception | None = None
    for attempt in range(3):
        db = _session()
        try:
            _begin_immediate(db)
            reason = _blocking_reason(
                db,
                lead_id=lead_id,
                to_email=email_norm,
                stage=stage_norm,
                digest=digest,
            )
            if reason:
                db.rollback()
                return SendClaim(proceed=False, key=key, reason=reason)

            existing = (
                db.query(EmailSendLedger)
                .filter(EmailSendLedger.idempotency_key == key)
                .first()
            )
            now = datetime.utcnow()
            if existing is not None and existing.status in BLOCKING:
                db.rollback()
                return SendClaim(
                    proceed=False,
                    key=key,
                    reason=_reason_for(existing, stage_norm, digest),
                )
            if existing is not None and existing.status == FAILED:
                existing.status = CLAIMED
                existing.error = None
                existing.claimed_at = now
                existing.updated_at = now
                existing.lead_id = lead_id or existing.lead_id
                existing.subject = (subject or "")[:500] or existing.subject
                existing.message_id = None
                existing.sent_at = None
                db.commit()
                return SendClaim(proceed=True, key=key)

            db.add(EmailSendLedger(
                idempotency_key=key,
                lead_id=lead_id,
                to_email=email_norm,
                stage=stage_norm or None,
                body_hash=digest,
                subject=(subject or "")[:500] or None,
                status=CLAIMED,
                claimed_at=now,
                updated_at=now,
            ))
            db.commit()
            return SendClaim(proceed=True, key=key)
        except IntegrityError as exc:
            last_error = exc
            db.rollback()
            continue
        except OperationalError as exc:
            last_error = exc
            db.rollback()
            if not _is_locked(exc) or attempt == 2:
                raise
            time.sleep(0.05 * (2 ** attempt))
            continue
        finally:
            db.close()

    raise RuntimeError(f"send ledger claim failed for key={key}") from last_error


def _is_locked(exc: BaseException) -> bool:
    from app.database.database import is_sqlite_locked
    return is_sqlite_locked(exc)


def _load_row(db: Session, key: str) -> EmailSendLedger | None:
    return (
        db.query(EmailSendLedger)
        .filter(EmailSendLedger.idempotency_key == key)
        .first()
    )


def _apply_success_markers(db: Session, row: EmailSendLedger, message_id: str) -> None:
    """Mirror a proven send into the lead and the workflow log.

    Done in the same transaction as the SENT transition so a caller rollback
    cannot leave SMTP success invisible to cadence.
    """
    if not row.lead_id:
        return
    from app.models.models import B2BLead, WorkflowEvent

    lead = db.query(B2BLead).filter(B2BLead.id == row.lead_id).first()
    if lead is not None:
        current = (lead.status or "").upper()
        if current not in _DO_NOT_DOWNGRADE:
            lead.status = "EMAIL_SENT"
        lead.email_sequence_last_sent = row.sent_at or datetime.utcnow()
        index = _STAGE_INDEX.get(row.stage or "")
        if index and (lead.email_sequence_stage or 0) < index:
            lead.email_sequence_stage = index

    to = row.to_email or ""
    mid = (message_id or "").strip()
    if not to or not mid:
        # Without both, the proof listener would rename the event and cadence
        # would ignore it. The ledger row is still SENT and is enough to
        # block a repeat; do not write an unproven EMAIL_SENT.
        return
    db.add(WorkflowEvent(
        lead_id=row.lead_id,
        event_type="EMAIL_SENT",
        actor="EMAIL_LEDGER",
        channel="email",
        payload={
            "to": to,
            "message_id": mid,
            "stage": row.stage or "",
            "subject": row.subject or "",
            "source": "email_send_ledger",
        },
        occurred_at=row.sent_at or datetime.utcnow(),
    ))


def _commit_mutation(key: str, mutate) -> None:
    last_error: Exception | None = None
    for attempt in range(6):
        db = _session()
        try:
            row = _load_row(db, key)
            if row is None:
                raise RuntimeError(f"send ledger row missing for key={key}")
            mutate(db, row)
            db.commit()
            return
        except OperationalError as exc:
            last_error = exc
            db.rollback()
            if not _is_locked(exc) or attempt == 5:
                raise
            time.sleep(0.05 * (2 ** attempt))
        finally:
            db.close()
    if last_error:
        raise last_error


def mark_sent(
    key: str,
    *,
    message_id: str,
    lead_id: int | None = None,
    to_email: str | None = None,
    stage: str | None = None,
    subject: str | None = None,
) -> None:
    """Record SMTP acceptance. A later caller rollback must not undo this."""
    if not key:
        return

    def mutate(db: Session, row: EmailSendLedger) -> None:
        now = datetime.utcnow()
        row.status = SENT
        row.message_id = (message_id or "")[:500] or None
        row.sent_at = now
        row.updated_at = now
        row.error = None
        if lead_id and not row.lead_id:
            row.lead_id = lead_id
        if to_email:
            row.to_email = normalize_email(to_email) or row.to_email
        if stage and not row.stage:
            row.stage = _stage(stage) or None
        if subject and not row.subject:
            row.subject = subject[:500]
        _apply_success_markers(db, row, message_id or "")

    _commit_mutation(key, mutate)


def mark_failed(key: str, error: str) -> None:
    """Record a send that did not leave. This must not look like EMAIL_SENT."""
    if not key:
        return

    def mutate(db: Session, row: EmailSendLedger) -> None:
        # Never downgrade a row we already proved. A late failure report
        # after acceptance is how a real send gets retried.
        if row.status == SENT:
            return
        row.status = FAILED
        row.error = (error or "")[:500]
        row.updated_at = datetime.utcnow()
        row.sent_at = None
        row.message_id = None

    _commit_mutation(key, mutate)
