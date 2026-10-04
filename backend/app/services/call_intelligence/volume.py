"""Fleet-wide AI-call volume caps (advisory to the scheduler; only ever REDUCE
volume).

AI_CALL_CYCLE_MAX_DIALS  max dials one worker cycle may place (default 5).
AI_CALL_MAX_INFLIGHT     max calls outstanding at once (default 2). A lead
                         counts as in flight while call_status=CALLING and
                         its last dial is newer than AI_CALL_INFLIGHT_WINDOW_MIN
                         (default 15; older CALLING rows are the reconciler's).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta


def _int_env(name: str, default: int, lo: int = 0) -> int:
    try:
        return max(lo, int(str(os.getenv(name, "")).strip() or default))
    except ValueError:
        return default


def cycle_max_dials() -> int:
    return _int_env("AI_CALL_CYCLE_MAX_DIALS", 5)


def max_inflight() -> int:
    return _int_env("AI_CALL_MAX_INFLIGHT", 2)


def inflight_window_minutes() -> int:
    return _int_env("AI_CALL_INFLIGHT_WINDOW_MIN", 15, lo=1)


def inflight_count(db, now: datetime | None = None) -> int:
    from app.models.models import B2BLead
    now = now or datetime.utcnow()
    since = now - timedelta(minutes=inflight_window_minutes())
    return (db.query(B2BLead)
              .filter(B2BLead.call_status == "CALLING",
                      B2BLead.last_call_date.isnot(None),
                      B2BLead.last_call_date >= since)
              .count())


def dial_budget(db, limit: int | None = None, now: datetime | None = None) -> int:
    """How many dials this cycle may place: min(explicit limit, cycle cap,
    free in-flight slots). Never negative."""
    cap = cycle_max_dials()
    if limit is not None:
        cap = min(cap, max(0, int(limit)))
    free = max(0, max_inflight() - inflight_count(db, now=now))
    return max(0, min(cap, free))
