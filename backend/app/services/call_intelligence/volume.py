"""Fleet-wide AI-call volume caps. The ceilings live in outbound_limits.

The environment can lower a ceiling. It cannot raise the live values of
1 dial per cycle and 1 call in flight. A lead counts as in flight while
call_status=CALLING and its last dial is newer than
AI_CALL_INFLIGHT_WINDOW_MIN (default 15; older CALLING rows are the
reconciler's).
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
    from app.services.outbound_limits import effective
    return effective("AI_CALL_CYCLE_MAX_DIALS")


def max_inflight() -> int:
    from app.services.outbound_limits import effective
    return effective("AI_CALL_MAX_INFLIGHT")


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
