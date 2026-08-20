"""
Multi-touch cadence.

Most B2B supply conversations need four to seven touches. Single-touch is not a
small inefficiency — it is the difference between a 2% and an 8% reply rate on
the same list.

Three rules the cadence must never break:

  EXIT ON ANY REPLY.  A human answered; the machine stops talking. Sending a
  scheduled "just following up" after a buyer has written to you is the single
  most relationship-damaging thing an outreach system can do.

  APPROVAL PER TOUCH.  Every touch is a draft in the approval queue. The
  cadence decides WHEN and WHAT, never whether to send. Nothing auto-sends.
  The founder records consent in founder_actions; the worker then drains.

  TRUST IS CHECKED AT SEND TIME, not at enrolment. A contact that bounces or
  expires mid-sequence drops out on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Day offset -> (touch name, purpose). The tone shifts deliberately: the early
# touches add value, the late ones ask plainly, and the last one gives the
# buyer a clean way to say no. A sequence with no exit ramp gets marked spam.
CADENCE = [
    (0,  "intro",        "category-specific opening"),
    (3,  "nudge",        "short follow-up, new subject line"),
    (7,  "proof",        "what comparable businesses buy and why"),
    (12, "ask",          "direct: 15-minute call or a sample"),
    (21, "breakup",      "should I close your file?"),
]
MAX_TOUCHES = len(CADENCE)

# Deliverability guard. The domain has been blocked once already; a cold
# domain that suddenly sends hundreds gets filtered, and every contact burned
# that way is unrecoverable.
DAILY_COLD_CAP = 35

TERMINAL_REASONS = ("replied", "bounced", "unsubscribed", "not_sendable",
                    "completed")

SIG = "Hiten Jain\nPurity Beans / Pure Pantry Provisions\nAbohar, Punjab"

OPEN_DRAFT_STATUSES = ("DRAFT", "PENDING", "EDITED", "FOUNDER_APPROVED", "QUEUED")


def _now() -> datetime:
    return datetime.utcnow()


def state(lead, db) -> dict:
    """
    Where is this contact in its sequence, computed from the event log rather
    than a status column. A column drifts; the log is what actually happened.
    """
    from app.models.models import WorkflowEvent
    from app.services.trust_promoter import REPLY_EVENTS, may_send

    evs = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id).order_by(
        WorkflowEvent.occurred_at.asc()).all()

    sends = [e for e in evs if e.event_type == "EMAIL_SENT"]
    replies = [e for e in evs if e.event_type in REPLY_EVENTS]
    bounced = [e for e in evs if e.event_type in ("EMAIL_BOUNCED", "HARD_BOUNCE")]
    unsub = [e for e in evs if e.event_type == "UNSUBSCRIBED"]

    if replies:
        return {"active": False, "reason": "replied", "touches": len(sends),
                "next_touch": None, "due": None,
                "note": "a human answered — the cadence stops here"}
    if bounced:
        return {"active": False, "reason": "bounced", "touches": len(sends),
                "next_touch": None, "due": None}
    if unsub:
        return {"active": False, "reason": "unsubscribed", "touches": len(sends),
                "next_touch": None, "due": None}

    ok, why = may_send(lead)
    if not ok:
        return {"active": False, "reason": "not_sendable", "touches": len(sends),
                "next_touch": None, "due": None, "note": why}

    n = len(sends)
    if n >= MAX_TOUCHES:
        return {"active": False, "reason": "completed", "touches": n,
                "next_touch": None, "due": None,
                "note": "sequence finished with no reply — move to nurture"}

    if n == 0:
        # `ready` was missing here, so due_now() — which tests st.get("ready")
        # — silently skipped every contact that had never been emailed. The
        # queue showed 2 when 31 were due. A never-contacted verified lead is
        # the most ready thing in the system, not the least.
        return {"active": True, "reason": "not_started", "touches": 0,
                "next_touch": CADENCE[0][1], "purpose": CADENCE[0][2],
                "due": _now(), "overdue_days": 0, "ready": True}

    first = sends[0].occurred_at or _now()
    last = sends[-1].occurred_at or _now()
    offset, name, purpose = CADENCE[n]
    due = first + timedelta(days=offset)
    # Never stack two touches in one day even if the schedule slipped.
    due = max(due, last + timedelta(days=2))
    return {"active": True, "reason": "in_sequence", "touches": n,
            "next_touch": name, "purpose": purpose, "due": due,
            "overdue_days": max(0, (_now() - due).days),
            "ready": _now() >= due}


def due_now(db, limit: int = 0) -> list[dict]:
    """Everything whose next touch has come due. Ordered by how long it waited."""
    from app.models.models import B2BLead
    from app.services.trust_promoter import may_send

    out = []
    for l in db.query(B2BLead).filter(B2BLead.email != "",
                                      B2BLead.email.isnot(None)).all():
        if not may_send(l)[0]:
            continue
        st = state(l, db)
        if st["active"] and st.get("ready"):
            out.append({"lead_id": l.id, "company": l.company, "city": l.city,
                        "fit": getattr(l, "coffee_buying_score", 0) or 0,
                        "category": l.division, "email": l.email,
                        "touch": st["next_touch"], "purpose": st["purpose"],
                        "touch_number": st["touches"] + 1,
                        "overdue_days": st["overdue_days"],
                        "confidence": getattr(l, "email_confidence", None)})
    # Highest confidence first: if the daily cap bites, spend it on the
    # contacts most likely to land rather than whoever sorted first.
    # Fit first, then confidence, then how overdue.
    #
    # Sorting on confidence alone ranked "we are sure we can reach them" above
    # "they are likely to buy", which is why three hospitals and two coworking
    # spaces — coffee_buying_score 0 — sat ahead of a cafe scoring 95 and three
    # hotels scoring 90. Deliverability decides IF a message can land; fit
    # decides whether it is worth landing.
    out.sort(key=lambda x: (-(x.get("fit") or 0),
                            -(x["confidence"] or 0),
                            -x["overdue_days"]))
    return out[:limit] if limit else out


def sent_today(db) -> int:
    from app.models.models import WorkflowEvent
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "EMAIL_SENT",
        WorkflowEvent.occurred_at >= start).count()


def prepare_due_drafts(db, cap: int = DAILY_COLD_CAP) -> dict:
    """
    Materialise due sequence touches as EmailDraft rows for founder approval.

    Called by the worker every cycle. Nothing is sent. Nothing is auto-approved.
    The founder's only job is to approve or reject rows in status=DRAFT; the
    worker drain then delivers approved ones.

    Skips contacts that already have an open draft, that the decision engine
    would refuse, or that the account frequency cap blocks.
    """
    from app.models.models import B2BLead, EmailDraft
    from app.services.outreach_engine import build_draft
    from app.services.account_graph import can_contact_new
    from app.services.decision_engine import evaluate_next_action

    used = sent_today(db)
    room = max(0, cap - used)
    due = due_now(db)
    picked = due[:room]

    created, skipped, failed = [], [], []

    for item in picked:
        l = db.query(B2BLead).filter(B2BLead.id == item["lead_id"]).first()
        if not l:
            continue

        decision = evaluate_next_action(l, db)
        if decision["action"] not in ("SEND", "DRAFT_ONLY"):
            skipped.append({**item, "why": decision["reason"]})
            continue

        allowed, why = can_contact_new(l, db)
        if not allowed:
            skipped.append({**item, "why": why})
            continue

        existing = db.query(EmailDraft).filter(
            EmailDraft.lead_id == l.id,
            EmailDraft.status.in_(OPEN_DRAFT_STATUSES),
        ).first()
        if existing:
            skipped.append({**item, "why": f"open draft #{existing.id} ({existing.status})"})
            continue

        try:
            d = build_draft(
                l, None, item["touch_number"] - 1, False,
                item["overdue_days"] or None, SIG,
            )
            row = EmailDraft(
                lead_id=l.id,
                follow_up_type=item["touch"],
                subject=d.get("subject"),
                body=d.get("body"),
                reason=(
                    f"sequence touch {item['touch_number']}: {item['purpose']} — "
                    f"awaiting founder approval"
                ),
                status="DRAFT",
                recipient=l.email,
            )
            db.add(row)
            created.append({
                "lead_id": l.id,
                "company": l.company,
                "touch": item["touch"],
                "touch_number": item["touch_number"],
                "email": l.email,
            })
        except Exception as e:
            failed.append({**item, "error": f"{e.__class__.__name__}: {e}"})

    if created:
        db.commit()

    return {
        "due": len(due),
        "sent_today": used,
        "cap": cap,
        "room_left": room,
        "created": len(created),
        "skipped": len(skipped),
        "failed": len(failed),
        "detail_created": created[:20],
        "detail_skipped": skipped[:10],
        "detail_failed": failed[:10],
        "founder_next": (
            f"{len(created)} draft(s) ready at GET /api/v1/founder/pending"
            if created else "nothing new for founder this cycle"
        ),
    }


def build_queue(db, cap: int = DAILY_COLD_CAP) -> dict:
    """
    In-memory preview of today's approval queue. Prefer prepare_due_drafts for
    the live path — that persists EmailDraft rows the founder actually decides.
    """
    from app.models.models import B2BLead
    from app.services.outreach_engine import build_draft
    from app.services.account_graph import can_contact_new

    used = sent_today(db)
    room = max(0, cap - used)
    due = due_now(db)
    picked, drafts, failed = due[:room], [], []

    governed = []
    for item in picked:
        l = db.query(B2BLead).filter(B2BLead.id == item["lead_id"]).first()
        if not l:
            continue
        allowed, why = can_contact_new(l, db)
        if not allowed:
            governed.append({**item, "blocked_by": why})
            continue
        try:
            d = build_draft(l, None, item["touch_number"] - 1, False,
                            item["overdue_days"] or None, SIG)
            drafts.append({**item, "subject": d.get("subject"),
                           "body": d.get("body")})
        except Exception as e:
            failed.append({**item, "error": f"{e.__class__.__name__}: {e}"})

    return {"due": len(due), "sent_today": used, "cap": cap,
            "room_left": room, "queued": len(drafts), "drafts": drafts,
            "failed": failed, "governed": governed,
            "blocked_by_account_cap": len(governed),
            "held_back": max(0, len(due) - room)}


def overview(db) -> dict:
    """Sequence health across the book — what the founder actually needs to see."""
    from app.models.models import B2BLead
    from collections import Counter
    c, touches = Counter(), Counter()
    for l in db.query(B2BLead).filter(B2BLead.email != "",
                                      B2BLead.email.isnot(None)).all():
        st = state(l, db)
        c[st["reason"]] += 1
        touches[st["touches"]] += 1
    return {"by_state": dict(c), "by_touch_count": dict(sorted(touches.items())),
            "due_today": len(due_now(db)), "sent_today": sent_today(db),
            "cap": DAILY_COLD_CAP}
