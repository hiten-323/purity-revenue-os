"""
Central sample-wording guard (3 Nov 2026, Hiten: "no sample wording anywhere in
outbound copy unless the lead's own inbound message asked for samples").

Registered on EmailDraft and OutboundWhatsApp as SQLAlchemy before_insert /
before_update listeners (same pattern as phone_intake.register on B2BLead), so
every ORM write of an outbound draft passes through it, whichever module wrote it.

A row whose subject/body (email) or message (WhatsApp) contains sample wording is
set to status BLOCKED_SAMPLE_WORDING — never silently sent, never silently
rewritten — unless the lead has an inbound record asking for samples. If the
inbound lookup itself fails, the row is blocked (fail closed).

Inbound evidence = a lead_interactions row for that lead, recorded on a real
channel (phone / whatsapp / email / manual), not a backfill of our own sends,
whose outcome/remark says the lead asked for samples (e.g. "SAMPLE_REQUEST:
requested samples for tasting").

Rows already SENT / CANCELLED / SUPERSEDED / BLOCKED_* are left alone: the guard
never rewrites history, and a row that has gone out cannot be recalled here.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import event, text as _sql

_log = logging.getLogger("sample_guard")

BLOCK_STATUS = "BLOCKED_SAMPLE_WORDING"
SAMPLE_RX = re.compile(r"\bsamples?\b|\bsampling\b|\btrial\s+packs?\b|\btasting\s+(jars?|kits?|packs?)\b", re.I)
_LEFT_ALONE = ("SENT", "CANCELLED", "SUPERSEDED", "DELIVERED", "READ")
_INBOUND_METHODS = ("phone", "whatsapp", "email", "manual")
_ASK_RX = re.compile(
    r"SAMPLE_REQUEST|\b(asked|asks|asking|requested|requests|requesting|wants?|wanted|needs?)\s+"
    r"(for\s+)?(a\s+|some\s+|the\s+)?samples?\b|\bsend\s+(us|me)\s+(a\s+|some\s+)?samples?\b|"
    r"\bsamples?\s+(request|requested)\b", re.I)


def has_sample_wording(*parts: str) -> str:
    m = SAMPLE_RX.search("\n".join(p or "" for p in parts))
    return m.group(0) if m else ""


def lead_asked_for_samples(connection, lead_id) -> bool:
    """True only on positive inbound evidence. Raises on lookup failure (caller fails closed)."""
    if not lead_id:
        return False
    rows = connection.execute(_sql(
        "select coalesce(outcome,'') || ' ' || coalesce(remark,'') from lead_interactions "
        "where lead_id = :lid and lower(coalesce(method,'')) in ('phone','whatsapp','email','manual') "
        "and coalesce(created_by,'') not like 'backfill%'"), {"lid": int(lead_id)}).fetchall()
    return any(_ASK_RX.search(r[0] or "") for r in rows)


def _status_blocked_already(status: str) -> bool:
    s = (status or "").upper()
    return s in _LEFT_ALONE or s.startswith("BLOCKED")


def _guard(target, connection, parts, note_attr):
    if _status_blocked_already(getattr(target, "status", "") or ""):
        return
    hit = has_sample_wording(*parts)
    if not hit:
        return
    try:
        allowed = lead_asked_for_samples(connection, getattr(target, "lead_id", None))
        why = "" if allowed else "no inbound sample request from this lead"
    except Exception as exc:  # noqa: BLE001 - fail closed
        allowed, why = False, f"inbound check failed ({exc.__class__.__name__})"
    if allowed:
        return
    prev = getattr(target, "status", None)
    target.status = BLOCK_STATUS
    note = f"BLOCKED_SAMPLE_WORDING ({hit!r}; {why}; was {prev})"
    if note_attr:
        cur = getattr(target, note_attr, None) or ""
        if note not in cur:
            setattr(target, note_attr, (cur + " | " if cur else "") + note)
    _log.warning("sample guard: %s id=%s lead=%s %s", target.__class__.__name__,
                 getattr(target, "id", None), getattr(target, "lead_id", None), note)


def _email(mapper, connection, target):
    _guard(target, connection, (target.subject, target.body), "reason")


def _whatsapp(mapper, connection, target):
    note = "whatsapp_response" if not (getattr(target, "whatsapp_response", None)) else None
    _guard(target, connection, (target.message,), note)


def register(email_cls=None, whatsapp_cls=None) -> None:
    """Idempotent."""
    if email_cls is None or whatsapp_cls is None:
        from app.models.models import EmailDraft, OutboundWhatsApp
        email_cls = email_cls or EmailDraft
        whatsapp_cls = whatsapp_cls or OutboundWhatsApp
    for cls, fn in ((email_cls, _email), (whatsapp_cls, _whatsapp)):
        for ev in ("before_insert", "before_update"):
            if not event.contains(cls, ev, fn):
                event.listen(cls, ev, fn)
