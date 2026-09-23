"""Single outbound-email business-hours gate.

All outbound prospect sends use Asia/Kolkata local time. The gate is fail-closed:
outside the configured window, sends are held before SMTP is contacted.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_START = "09:00"
DEFAULT_END = "18:00"


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


def is_open(now: datetime | None = None) -> bool:
    if os.getenv("OUTREACH_BUSINESS_HOURS", "1").strip().lower() in {"0", "false", "no", "off"}:
        return True
    local = (now or datetime.now(IST)).astimezone(IST)
    start, end = window()
    return start <= local.time() <= end


def next_open(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(IST)).astimezone(IST)
    start, _ = window()
    if is_open(local):
        return local
    candidate = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if local.time() > start:
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
        "open": is_open(local),
    }
