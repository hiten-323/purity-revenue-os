"""Single outbound-email business-hours gate.

All outbound prospect sends use Asia/Kolkata local time. The gate is fail-closed:
outside the configured window, sends are held before SMTP is contacted.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_START = "09:30"
DEFAULT_END = "19:30"  # 2026-10-24 founder: email 09:30-19:30 IST every day


def _parse(value: str, fallback: str) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return time.fromisoformat(fallback)


def window() -> tuple[time, time]:
    return (
        _parse(os.getenv("OUTREACH_BUSINESS_HOURS_START", DEFAULT_START), DEFAULT_START),
        _parse(os.getenv("OUTREACH_BUSINESS_HOURS_END", DEFAULT_END), DEFAULT_END),
    )


def _sunday_allowed() -> bool:
    return os.getenv("OUTREACH_ALLOW_SUNDAY", "0").strip().lower() in {"1", "true", "yes", "on"}


def is_open(now: datetime | None = None) -> bool:
    if os.getenv("OUTREACH_BUSINESS_HOURS", "1").strip().lower() in {"0", "false", "no", "off"}:
        return True
    local = (now or datetime.now(IST)).astimezone(IST)
    # Sunday (weekday 6) is strictly closed for outbound commercial communication
    if local.weekday() == 6 and not _sunday_allowed():
        return False
    start, end = window()
    return start <= local.time() <= end


def next_open(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(IST)).astimezone(IST)
    start, _ = window()
    if is_open(local):
        return local
    candidate = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if local.time() > start or (local.weekday() == 6 and not _sunday_allowed()):
        candidate += timedelta(days=1)
    while candidate.weekday() == 6 and not _sunday_allowed():
        candidate += timedelta(days=1)
    return candidate


def status(now: datetime | None = None) -> dict:
    local = (now or datetime.now(IST)).astimezone(IST)
    start, end = window()
    return {
        "timezone": "Asia/Kolkata",
        "local_time": local.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "day_of_week": local.strftime("%A"),
        "open": is_open(local),
    }
