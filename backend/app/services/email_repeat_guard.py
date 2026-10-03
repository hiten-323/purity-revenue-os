"""Per-lead repeat-send guard for prospect email.

Incident (2026-10-03): AltSpace (lead 1345) received 45 automated emails in
four days, roughly one per worker cycle, and FabHotel Yellow Leaf (lead 266)
received 37. Every send was recorded correctly (OutreachTouch SENT and
WorkflowEvent EMAIL_SENT with a message id); the record was never the problem.
decision_engine answered DRAFT_ONLY ("may write, not send") and
smart_outreach.plan_touch only refused SUPPRESS / WAIT / NONE / ENRICH /
FOUNDER_REVIEW, so DRAFT_ONLY fell through to the cadence branch and a
"WARM_FOLLOW_UP" went out on every cycle, even after the five-touch sequence
had completed.

That gap is closed in plan_touch. This module is the backstop that does not
depend on any planner being right: one email per lead per
EMAIL_LEAD_MIN_GAP_HOURS (default 72) unless the buyer engaged after the last
send, plus a per-lead hold list (EMAIL_HOLD_LEAD_IDS) for immediate, reversible
operator blocks. It is checked at the send_email chokepoint and again, with
in-flight reservations counted, in smart_outreach.execute_one.

Every refusal starts with "HELD:" — a retry-later state, not a failure of the
lead.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

PROVEN_SEND = ("SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ")
RESERVED = "SENDING"

# Something the buyer (or a call with them) did after our last email. A
# conversation in progress may be answered inside the gap; a cadence may not.
ENGAGEMENT_EVENTS = (
    "EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY",
    "FOUNDER_CALL", "NEXT_ACTION_SET", "CATALOGUE_REQUESTED",
)


def hold_ids() -> set[int]:
    raw = os.getenv("EMAIL_HOLD_LEAD_IDS", "") or ""
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def min_gap_hours() -> float:
    try:
        return max(0.0, float(os.getenv("EMAIL_LEAD_MIN_GAP_HOURS", "72")))
    except ValueError:
        return 72.0


def last_send_at(db, lead_id: int, *, include_reservations: bool = False,
                 to_email: str | None = None):
    """Most recent proven (optionally in-flight) email to this lead."""
    from app.models.models import WorkflowEvent

    times = []
    ev = (db.query(WorkflowEvent.occurred_at)
          .filter(WorkflowEvent.lead_id == lead_id,
                  WorkflowEvent.event_type == "EMAIL_SENT")
          .order_by(WorkflowEvent.occurred_at.desc()).first())
    if ev and ev[0]:
        times.append(ev[0])
    try:
        from app.services.smart_outreach import OutreachTouch
        statuses = PROVEN_SEND + ((RESERVED,) if include_reservations else ())
        t = (db.query(OutreachTouch.occurred_at)
             .filter(OutreachTouch.lead_id == lead_id,
                     OutreachTouch.channel == "email",
                     OutreachTouch.status.in_(statuses))
             .order_by(OutreachTouch.occurred_at.desc()).first())
        if t and t[0]:
            times.append(t[0])
    except Exception:
        # outreach_touches missing would be a schema problem, not permission;
        # the WorkflowEvent ledger above is still authoritative.
        pass
    if to_email:
        # Same mailbox under a different lead id (duplicate records, branches).
        addr = to_email.strip().lower()
        cutoff = datetime.utcnow() - timedelta(hours=max(min_gap_hours(), 1))
        rows = (db.query(WorkflowEvent.occurred_at, WorkflowEvent.payload)
                .filter(WorkflowEvent.event_type == "EMAIL_SENT",
                        WorkflowEvent.occurred_at >= cutoff).all())
        for occ, payload in rows:
            if occ and isinstance(payload, dict) and \
                    (payload.get("to") or "").strip().lower() == addr:
                times.append(occ)
    return max(times) if times else None


def engaged_since(db, lead_id: int, since) -> bool:
    from app.models.models import WorkflowEvent
    return db.query(WorkflowEvent.id).filter(
        WorkflowEvent.lead_id == lead_id,
        WorkflowEvent.event_type.in_(ENGAGEMENT_EVENTS),
        WorkflowEvent.occurred_at > since).first() is not None


def check(db, lead_id: int, *, to_email: str | None = None,
          include_reservations: bool = False, now: datetime | None = None
          ) -> tuple[bool, str]:
    """(may_send, why). Raises only on DB failure; callers fail closed."""
    if lead_id in hold_ids():
        return False, "HELD: lead is on EMAIL_HOLD_LEAD_IDS (operator hold)"
    gap = min_gap_hours()
    if gap <= 0:
        return True, "repeat guard disabled (EMAIL_LEAD_MIN_GAP_HOURS=0)"
    last = last_send_at(db, lead_id, include_reservations=include_reservations,
                        to_email=to_email)
    if last is None:
        return True, "no prior email"
    now = now or datetime.utcnow()
    age_h = (now - last).total_seconds() / 3600.0
    if age_h >= gap:
        return True, f"last email {age_h:.0f}h ago"
    if engaged_since(db, lead_id, last):
        return True, f"buyer engaged since last email ({age_h:.1f}h ago)"
    return False, (f"HELD: last email to this lead {age_h:.1f}h ago — at most "
                   f"one email per {gap:g}h unless they reply")
