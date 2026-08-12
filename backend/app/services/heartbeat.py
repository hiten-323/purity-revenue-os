"""
Component heartbeats.

pm2 reporting "online" only means a process exists. The worker's reply poller
ran for hours today logging "ZOHO_APP_PASSWORD not configured" on every cycle
— alive, scheduled, and doing nothing — while pm2 and /health both said
healthy. A process that is running but not working is the failure mode that
costs revenue quietly, and nothing in the system could see it.

So each background component writes a timestamp when it completes real work.
Health then asks a different question: not "is the process up?" but "has this
component done its job recently?"

Deliberately kept to ONE cheap table. A health endpoint that grows to probe
SMTP, IMAP, Cloudflare and every external API becomes a 2-second request that
itself fails under load — and then the watchdog restarts a healthy system
because its health check timed out. External integrations get their own
readiness metrics; /health only covers what is needed to serve a request.
"""
from __future__ import annotations

from datetime import datetime

# How stale a beat may be before the component is considered dead. Set per
# component from its actual cadence, not a single global guess: the worker
# cycles every 600s, so a 120s threshold would report it dead most of the time.
STALE_AFTER = {
    "worker": 900,          # 600s cycle + margin
    "reply_poller": 900,
    "send_queue": 900,
    "harvester": 86400,     # runs on demand, not on a schedule
    "sequence_engine": 900,
}
DEFAULT_STALE = 900


def beat(component: str, db, detail: dict | None = None) -> None:
    """
    Record that a component just finished real work.

    Call this AFTER the work, never before — a beat written at the top of a
    loop proves the loop started, which is exactly the useless signal pm2
    already gives us.
    """
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(
        lead_id=None, event_type="HEARTBEAT", actor=component,
        channel="ops", payload={"component": component, "detail": detail or {}},
        occurred_at=datetime.utcnow()))
    db.commit()


def last_beat(component: str, db):
    from app.models.models import WorkflowEvent
    row = db.query(WorkflowEvent.occurred_at).filter(
        WorkflowEvent.event_type == "HEARTBEAT",
        WorkflowEvent.actor == component
    ).order_by(WorkflowEvent.occurred_at.desc()).first()
    return row[0] if row else None


def status(db, components: tuple = ("worker",)) -> dict:
    """
    Per-component liveness. Returns (dict, all_ok) style payload where each
    entry says how long ago it worked and whether that is acceptable.
    """
    out, ok = {}, True
    now = datetime.utcnow()
    for c in components:
        ts = last_beat(c, db)
        limit = STALE_AFTER.get(c, DEFAULT_STALE)
        if ts is None:
            out[c] = {"status": "unknown", "age_seconds": None,
                      "note": "no heartbeat recorded yet"}
            continue                      # unknown is not failure on first boot
        age = int((now - ts).total_seconds())
        alive = age <= limit
        out[c] = {"status": "ok" if alive else "stale", "age_seconds": age,
                  "limit_seconds": limit}
        if not alive:
            ok = False
    return {"components": out, "all_ok": ok}


def queue_health(db) -> dict:
    """
    Queue AGE, not just depth.

    12 pending with the oldest 2 minutes old is a system draining normally.
    12 pending with the oldest 18 hours old is a system that has stopped, and
    depth alone cannot tell those apart. Age is the signal; depth is context.
    """
    from app.models.models import WorkflowEvent
    from app.services.send_queue import pending, APPROVAL_EVENT

    now = datetime.utcnow()
    try:
        waiting = pending(db)
    except Exception as e:
        return {"error": f"{e.__class__.__name__}: {e}"[:100]}

    oldest_age = None
    if waiting:
        row = db.query(WorkflowEvent.occurred_at).filter(
            WorkflowEvent.event_type == APPROVAL_EVENT,
            WorkflowEvent.lead_id.in_([l.id for l in waiting])
        ).order_by(WorkflowEvent.occurred_at.asc()).first()
        if row and row[0]:
            oldest_age = int((now - row[0]).total_seconds())

    sent = db.query(WorkflowEvent.occurred_at).filter(
        WorkflowEvent.event_type == "EMAIL_SENT"
    ).order_by(WorkflowEvent.occurred_at.desc()).first()
    last_send_age = int((now - sent[0]).total_seconds()) if sent and sent[0] else None

    # Pending work that has not moved in hours means revenue has stopped, even
    # though every process is "up". This is the pair worth alerting on.
    stalled = bool(waiting) and (oldest_age or 0) > 7200 and \
        (last_send_age is None or last_send_age > 7200)

    return {"pending": len(waiting),
            "oldest_pending_seconds": oldest_age,
            "last_successful_send_seconds_ago": last_send_age,
            "stalled": stalled}
