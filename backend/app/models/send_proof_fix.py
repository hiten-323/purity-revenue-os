"""
Replace the fail-open EMAIL_SENT proof listener with a fail-closed one.

The original _require_send_proof in models.py:
  - opened a separate SessionLocal() for duplicate detection
  - swallowed any failure of that query and still left EMAIL_SENT proven

Both are wrong. The insert's own connection is the only view of the table that
is consistent with the write in progress, and if uniqueness cannot be checked
the event must not count as a proven send.

Imported from main.py after models so the original listener is detached first.
"""
from __future__ import annotations

import json

from sqlalchemy import event, text

from app.models.models import WorkflowEvent, EMAIL_SENT_UNPROVEN, _require_send_proof


# Detach the original listener. Leaving both would double-fire; removing only
# this reference is intentional — the function object identity is what sqlalchemy
# uses to match.
event.remove(WorkflowEvent, "before_insert", _require_send_proof)


def _payload_as_dict(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


@event.listens_for(WorkflowEvent, "before_insert")
def _require_send_proof_strict(mapper, connection, target):
    """
    Same invariant as before — EMAIL_SENT needs recipient + message_id — but:

      * duplicate scan uses `connection` (the insert's transaction)
      * any failure of that scan demotes to EMAIL_SENT_UNPROVEN

    Nothing is raised: a failed guard must not abort an unrelated write, but it
    also must not pretend a send was proven when it could not verify that.
    """
    if target.event_type != "EMAIL_SENT":
        return

    p = dict(target.payload or {})
    to = str(p.get("to") or "").strip()
    mid = str(p.get("message_id") or "").strip()

    if not (to and mid):
        missing = [n for n, v in (("recipient", to), ("message_id", mid)) if not v]
        target.event_type = EMAIL_SENT_UNPROVEN
        p["unproven_because"] = f"missing {', '.join(missing)}"
        p["note"] = (
            "recorded as a send but cannot prove delivery; excluded "
            "from send counts and cadence"
        )
        target.payload = p
        return

    # Same connection as the insert. A separate SessionLocal can see a different
    # snapshot, fail independently, and leave this write looking proven when the
    # check never ran — which is how duplicate message-ids would inflate cadence.
    try:
        rows = connection.execute(
            text(
                "SELECT id, payload FROM workflow_events "
                "WHERE event_type = 'EMAIL_SENT'"
            )
        ).fetchall()
        for row in rows:
            existing_id, existing_payload = row[0], _payload_as_dict(row[1])
            if str(existing_payload.get("message_id") or "") == mid:
                target.event_type = "EMAIL_SENT_DUPLICATE"
                p["duplicate_of_event"] = existing_id
                p["note"] = (
                    "this provider message-id is already recorded; "
                    "one acceptance, one delivery"
                )
                target.payload = p
                return
    except Exception as e:
        target.event_type = EMAIL_SENT_UNPROVEN
        p["unproven_because"] = (
            f"duplicate check failed: {e.__class__.__name__}: {e}"
        )
        p["note"] = (
            "could not verify message-id uniqueness on the insert connection; "
            "recorded unproven rather than risk a double-counted send"
        )
        target.payload = p
        return
    # provable and unique — leave as EMAIL_SENT
