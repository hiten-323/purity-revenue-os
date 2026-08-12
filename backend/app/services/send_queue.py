"""
Self-draining approved-send queue.

The founder approves once. The system paces the rest.

Until now a batch that hit the hourly cap simply stopped, and 21 approved
emails sat unsent waiting for a human to re-run a script. That is not a
throttle working correctly — it is an operational hole with a throttle in
front of it. Approval and pacing are different concerns and only one of them
needs a person.

THE SAFETY PROPERTY THAT MATTERS
An automatic sender must never send anything a human did not approve. So the
approval is a PERSISTED EVENT per lead, not an implicit property of being in
the queue. If no OUTREACH_APPROVED event exists for a lead, the drain will not
send to it — no matter how due, how verified, or how long it has waited.
Approvals are consumed on send, so one approval buys exactly one message.
"""
from __future__ import annotations

from datetime import datetime, timedelta

APPROVAL_EVENT = "OUTREACH_APPROVED"
CONSUMED_EVENT = "EMAIL_SENT"
APPROVAL_TTL_DAYS = 7      # stale approval is not consent to send today


def approve(lead, db, touch: str = "", note: str = "",
            approved_by: str = "FOUNDER") -> None:
    """Record founder approval for ONE outbound message to this contact."""
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(
        lead_id=lead.id, event_type=APPROVAL_EVENT, actor=approved_by,
        channel="approval",
        payload={"to": lead.email, "touch": touch, "note": note,
                 "approved_at": datetime.utcnow().isoformat()},
        occurred_at=datetime.utcnow()))


def pending(db) -> list:
    """
    Leads with an unconsumed, unexpired approval.

    An approval is consumed when a send follows it. Counting approvals against
    sends is what stops one approval authorising a whole sequence.
    """
    from app.models.models import B2BLead, WorkflowEvent
    # An approval is settled either by a real send or by an explicit
    # revocation. Both consume it; only one of them is a delivery.
    evs = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type.in_(
            [APPROVAL_EVENT, CONSUMED_EVENT, "APPROVAL_REVOKED"])
    ).order_by(WorkflowEvent.occurred_at.asc()).all()

    by_lead: dict = {}
    for e in evs:
        by_lead.setdefault(e.lead_id, []).append(e)

    out = []
    cutoff = datetime.utcnow() - timedelta(days=APPROVAL_TTL_DAYS)
    for lead_id, rows in by_lead.items():
        approvals = [e for e in rows if e.event_type == APPROVAL_EVENT
                     and (e.occurred_at or datetime.min) >= cutoff]
        settled = [e for e in rows
                   if e.event_type in (CONSUMED_EVENT, "APPROVAL_REVOKED")
                   and approvals
                   and (e.occurred_at or datetime.min)
                   >= (approvals[0].occurred_at or datetime.min)]
        if len(approvals) > len(settled):
            l = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
            if l:
                out.append(l)
    return out


# States a contact can enter that make an existing approval meaningless. An
# approval is consent to send to a CONTACT, not a licence that survives that
# contact changing underneath it.
def invalidate_stale(db) -> dict:
    """
    Revoke approvals whose contact can no longer be sent to.

    Time-based expiry alone is not enough. 12 approvals sat pending for four
    hours pointing at contacts whose addresses had since been demoted to
    VALIDATED or cleared entirely by the chain-inbox sweep. They could never
    be sent, so they blocked the queue, made "pending: 12" look like normal
    backlog, and pinned /health at 503 indefinitely.

    Revoking is the honest outcome: the founder approved sending to a verified
    address, and that address is gone. Re-approval should be a fresh decision,
    not an old one silently reused against different data.
    """
    from app.models.models import WorkflowEvent
    from app.services.trust_promoter import may_send

    revoked = []
    for l in pending(db):
        ok, why = may_send(l)
        if ok:
            continue
        revoked.append({"company": l.company, "email": l.email, "why": why})
        db.add(WorkflowEvent(
            lead_id=l.id, event_type="APPROVAL_REVOKED", actor="SYSTEM",
            channel="approval",
            payload={"to": l.email, "reason": why,
                     "note": "contact stopped being sendable after approval; "
                             "re-approval must be a fresh decision"},
            occurred_at=datetime.utcnow()))
        # APPROVAL_REVOKED consumes the approval on its own — see pending().
        # The first cut of this wrote an EMAIL_SENT to consume it, which would
        # have put a delivery in the log for a message that never left,
        # inflating "sent today" and telling the trust engine the address had
        # accepted mail. An event log that records sends which did not happen
        # is worse than no log at all.
    db.commit()
    return {"revoked": len(revoked), "detail": revoked}


def drain(db, max_per_run: int = 12, pace_seconds: int = 46) -> dict:
    """
    Send what is approved AND allowed, right now. Called on the worker's
    cycle; safe to call as often as you like because every limit is re-checked
    per message and the decision engine has the final word.
    """
    import time
    from app.models.models import WorkflowEvent
    from app.services.decision_engine import evaluate_next_action
    from app.services.email_sender import send_email, OutreachEmail
    from app.services.outreach_engine import build_draft
    from app.services.trust_promoter import on_delivery
    from app.services import sequence_engine as se

    SIG = "Hiten Jain\nPurity Beans / Pure Pantry Provisions\nAbohar, Punjab"
    report = {"approved_waiting": 0, "sent": 0, "held": 0, "blocked": 0,
              "failed": 0, "detail": []}

    # Revoke approvals whose contact stopped being sendable BEFORE counting
    # the queue, so a backlog of impossible sends never masquerades as normal
    # pending work — that is what kept /health at 503 for four hours.
    try:
        inv = invalidate_stale(db)
        if inv["revoked"]:
            report["revoked_stale"] = inv["revoked"]
    except Exception as e:
        print(f"[send_queue] invalidate_stale failed: {e}")

    todo = pending(db)
    report["approved_waiting"] = len(todo)
    if not todo:
        return report

    for l in todo[:max_per_run]:
        # The decision engine decides. This function does not second-guess it —
        # that separation is the whole reason the engine exists.
        d = evaluate_next_action(l, db)
        if d["action"] != "SEND":
            key = "held" if "delivery" in " ".join(d["blockers"]) else "blocked"
            report[key] += 1
            report["detail"].append(
                {"company": l.company, "result": key, "why": d["reason"]})
            if key == "held":
                # The delivery window is shut for EVERY lead, not this one.
                # Walking the rest of the queue to collect identical refusals
                # wastes a verification round-trip each and tells us nothing.
                report["detail"].append(
                    {"company": "-", "result": "stopped",
                     "why": "delivery window shut — rest of queue retained"})
                break
            continue

        st = se.state(l, db)
        try:
            draft = build_draft(l, None, st["touches"], False,
                                st.get("overdue_days") or None, SIG)
        except Exception as e:
            report["failed"] += 1
            report["detail"].append({"company": l.company, "result": "no_draft",
                                     "why": f"{e.__class__.__name__}: {e}"})
            continue

        e = OutreachEmail(to_email=l.email, to_name=l.contact_name or "",
                          company=l.company, subject=draft.get("subject"),
                          body_text=draft.get("body"), lead_id=l.id)
        r = send_email(e)
        if r.status == "sent":
            report["sent"] += 1
            db.add(WorkflowEvent(
                lead_id=l.id, event_type="EMAIL_SENT", actor="QUEUE_DRAIN",
                channel="email",
                # The provider's message-id is the only thing that proves SMTP
                # accepted this message. Without it the record says a send
                # happened but cannot show it did — which described 79 of 90
                # EMAIL_SENT events before this.
                payload={"to": l.email, "subject": draft.get("subject"),
                         "message_id": getattr(r, "message_id", "") or "",
                         "smtp_response": getattr(r, "smtp_response", "") or "",
                         "touch": st.get("next_touch"),
                         "touch_number": st["touches"] + 1,
                         "category": l.division,
                         "authorised_by": "founder approval on file"},
                occurred_at=datetime.utcnow()))
            on_delivery(l, db, delivered=True)
            db.commit()
            report["detail"].append({"company": l.company, "result": "sent",
                                     "to": l.email})
            time.sleep(pace_seconds)
        else:
            held = "HELD:" in (r.error or "")
            report["held" if held else "failed"] += 1
            db.add(WorkflowEvent(
                lead_id=l.id, event_type="EMAIL_FAILED", actor="QUEUE_DRAIN",
                channel="email",
                payload={"to": l.email, "smtp_error": (r.error or "")[:300]},
                occurred_at=datetime.utcnow()))
            db.commit()
            report["detail"].append({"company": l.company,
                                     "result": "held" if held else "failed",
                                     "why": (r.error or "")[:120]})
            if held:
                # The window is shut for everyone; stop rather than log 20
                # identical refusals and pollute the failure rate.
                break
    return report


def status(db) -> dict:
    """Queue health for the dashboard: is anything stuck, and for how long?"""
    from app.models.models import WorkflowEvent
    from app.services.timeutil import ist_str
    todo = pending(db)
    oldest = None
    if todo:
        ev = db.query(WorkflowEvent).filter(
            WorkflowEvent.lead_id.in_([l.id for l in todo]),
            WorkflowEvent.event_type == APPROVAL_EVENT
        ).order_by(WorkflowEvent.occurred_at.asc()).first()
        oldest = ev.occurred_at if ev else None
    return {"approved_waiting": len(todo),
            "oldest_approval": ist_str(oldest) if oldest else None,
            "waiting_hours": round((datetime.utcnow() - oldest).total_seconds() / 3600, 1)
                             if oldest else 0,
            "companies": [l.company for l in todo[:20]]}
