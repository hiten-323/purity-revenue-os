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
# 29 Oct (Hiten): only Hiten's own yes is an approval. Automation actors can
# neither write one nor have one counted by the drain.
FOUNDER_ACTORS = {"FOUNDER"}


def is_founder_actor(actor) -> bool:
    return (actor or "").strip().upper() in FOUNDER_ACTORS


def _auto_on() -> bool:
    try:
        from app.services import auto_send
        return auto_send.enabled()
    except Exception:
        return False


def is_approval_actor(actor) -> bool:
    """Founder approvals always count. 29 Oct 3:21 PM (Hiten, "fully automatic
    ... within the limits"): AUTO_SEND approvals count only while
    OUTREACH_AUTO_SEND=1, and only auto_send.py writes them after its gates."""
    if is_founder_actor(actor):
        return True
    from app.services.auto_send import AUTO_ACTOR
    return (actor or "").strip().upper() == AUTO_ACTOR and _auto_on()


def approve(lead, db, touch: str = "", note: str = "",
            approved_by: str = "FOUNDER") -> None:
    """Record founder approval for ONE outbound message to this contact."""
    from app.models.models import WorkflowEvent
    if not is_founder_actor(approved_by) and not (
            (approved_by or "").strip().upper() == "AUTO_SEND" and _auto_on()):
        raise PermissionError(
            f"approval refused: actor {approved_by!r} is not the founder; "
            "automation may only leave drafts awaiting approval")
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
                     and is_approval_actor(e.actor)
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
            try:
                from app.services.shared_address_rule import check as _shared_check
                ok, why = _shared_check(db, l.id, l.email)
            except Exception:
                ok = True   # send-time guard fails closed; do not revoke on error
        if ok:
            # 28 Oct: an approval to a bounced/suppressed address is revoked,
            # not left to fail at send time every cycle.
            try:
                from app.services.bounce_guard import check as _bounce_check
                ok, why = _bounce_check(l.email, db)
                if not ok:
                    why = f"BLOCKED: bounce guard - {why}"
            except Exception:
                ok = True   # send-time guard fails closed; do not revoke on error
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

    SIG = "Hiten Jain\nPurity Beans / Pure Pantry Provisions\nIndia\nWebsite: https://p3online.in"
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

    # 29 Oct: walk the WHOLE queue. Previously the head slice of max_per_run rows let the first
    # 12 permanently-blocked rows (suppressed leads) starve every clean one
    # behind them. max_per_run now bounds send ATTEMPTS, not evaluations.
    attempts = 0
    for l in todo:
        if attempts >= max_per_run:
            break
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
        from app.models.models import EmailDraft
        _ok_status = ["FOUNDER_APPROVED", "APPROVED"] + (["AUTO_APPROVED"] if _auto_on() else [])
        approved_d = (
            db.query(EmailDraft)
            .filter(
                EmailDraft.lead_id == l.id,
                EmailDraft.status.in_(_ok_status),
            )
            .order_by(EmailDraft.id.desc())
            .first()
        )
        if approved_d is not None and approved_d.status == "AUTO_APPROVED":
            # 29 Oct: an automatic approval re-runs every auto_send gate now.
            from app.services import auto_send as _as
            _a_ok, _a_why = _as.send_time_check(db, l, approved_d)
            if not _a_ok:
                # Revoke: the draft goes back to DRAFT, the approval is consumed.
                approved_d.status = "DRAFT"
                approved_d.reason = ((approved_d.reason or "") + f" | auto approval revoked: {_a_why}")[:1000]
                db.add(WorkflowEvent(
                    lead_id=l.id, event_type="APPROVAL_REVOKED", actor="AUTO_SEND",
                    channel="approval",
                    payload={"to": l.email, "reason": _a_why[:300], "draft_id": approved_d.id},
                    occurred_at=datetime.utcnow()))
                db.commit()
                report["blocked"] += 1
                report["detail"].append({"company": l.company, "result": "blocked",
                                         "why": f"auto gate: {_a_why}",
                                         "draft_id": approved_d.id})
                continue
        # 29 Oct: send only a draft Hiten approved. Never compose a fresh,
        # unreviewed message just because the lead has an approval on file.
        if not (approved_d and approved_d.body and approved_d.subject):
            report["blocked"] += 1
            report["detail"].append({"company": l.company, "result": "blocked",
                                     "why": "no founder-approved draft on file"})
            continue
        from app.services.wording_gate import check as _wording_check
        _w_ok, _w_why = _wording_check(approved_d.subject, approved_d.body)
        if not _w_ok:
            report["blocked"] += 1
            report["detail"].append({"company": l.company, "result": "blocked",
                                     "why": f"wording gate: {_w_why}",
                                     "draft_id": approved_d.id})
            continue
        if approved_d and approved_d.body and approved_d.subject:
            subject_to_send = approved_d.subject
            body_to_send = approved_d.body
            if "p3online.in" not in body_to_send:
                body_to_send = body_to_send.rstrip() + "\nWebsite: https://p3online.in"
        else:
            try:
                draft = build_draft(l, None, st["touches"], False,
                                    st.get("overdue_days") or None, SIG)
                subject_to_send = draft.get("subject")
                body_to_send = draft.get("body")
            except Exception as e:
                report["failed"] += 1
                report["detail"].append({"company": l.company, "result": "no_draft",
                                         "why": f"{e.__class__.__name__}: {e}"})
                continue

        # 29 Oct (Hiten): one email per company domain per 7 days.
        from app.services import domain_rule as _dr
        _dr_ok, _dr_why = _dr.check(db, l.email)
        if not _dr_ok:
            report["blocked"] += 1
            report["detail"].append({"company": l.company, "result": "blocked",
                                     "why": _dr_why})
            continue
        from app.services import human_pace as _hp
        _hp_ok, _hp_why = _hp.may_send_email()
        if not _hp_ok:
            report["held"] += 1
            report["detail"].append({"company": l.company, "result": "held",
                                     "why": _hp_why})
            break
        e = OutreachEmail(to_email=l.email, to_name=l.contact_name or "",
                          company=l.company, subject=subject_to_send,
                          body_text=body_to_send, lead_id=l.id)
        attempts += 1
        _was_auto = approved_d is not None and approved_d.status == "AUTO_APPROVED"
        r = send_email(e)
        if r.status == "sent":
            _hp.record_email_sent()
            if approved_d:
                approved_d.status = "SENT"
                approved_d.sent_at = datetime.utcnow()
            report["sent"] += 1
            db.add(WorkflowEvent(
                lead_id=l.id, event_type="EMAIL_SENT", actor="QUEUE_DRAIN",
                channel="email",
                payload={"to": l.email, "subject": subject_to_send,
                         "message_id": getattr(r, "message_id", "") or "",
                         "smtp_response": getattr(r, "smtp_response", "") or "",
                         "touch": st.get("next_touch"),
                         "touch_number": st["touches"] + 1,
                         "category": l.division,
                         "authorised_by": ("auto (Hiten 29 Oct 3:21 PM)" if _was_auto
                                           else "founder approval on file")},
                occurred_at=datetime.utcnow()))
            on_delivery(l, db, delivered=True)
            db.commit()
            report["detail"].append({"company": l.company, "result": "sent",
                                     "to": l.email})
            if not _hp.enabled():
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
