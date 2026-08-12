"""
One clock: store UTC, show IST.

WHY BOTH
Storage is UTC because every window in this system is arithmetic — the
deliverability guard's 24 hours, "quiet for 14 days", opportunity aging. Mixing
local and UTC writes put rows 5.5 hours in the future relative to those
comparisons, so a send could look older or newer than it was. 16 writes were
doing exactly that.

Display is IST because the founder is in Punjab and a timestamp they cannot read
at a glance is worse than no timestamp. "Called at 09:12" must mean 9:12 in the
morning where the buyer is.

So: never convert on the way in, always convert on the way out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def to_ist(dt: datetime | None) -> datetime | None:
    """A naive-UTC timestamp from the database, as IST."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_iso(dt: datetime | None) -> str | None:
    """ISO-8601 with the +05:30 offset, so the client cannot misread it."""
    d = to_ist(dt)
    return d.isoformat() if d else None


def ist_str(dt: datetime | None, fmt: str = "%d %b %Y, %I:%M %p") -> str:
    """Human-readable IST — '03 Aug 2026, 10:07 PM IST'."""
    d = to_ist(dt)
    return f"{d.strftime(fmt)} IST" if d else "not recorded"


def now_ist() -> datetime:
    return datetime.now(IST)
