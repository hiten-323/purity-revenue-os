"""
Callback Scheduler for Purity Revenue OS (Definite Answer Follow-Up Policy).

Enforces prospect-stated callback times as HIGHEST PRIORITY:
- Extracts prospect-stated dates, times, days of week, and relative windows.
- Resolves local timezone using Asia/Kolkata (IST, UTC+05:30) for Indian prospects.
- Enforces strict compliance: NEVER call before the prospect's requested time.
- If requested time falls outside legal business hours (TRAI 09:30-19:00 Mon-Sat),
  defers to the next permitted business-hours window strictly AFTER the requested time.
- Identifies ambiguous callback statements and flags them for human review without guessing.
- Persistent database state survives PM2 restarts, VPS reboots, and worker interruptions.
- Enforces atomic claim locks (lead_locked_until) to prevent multi-worker collisions.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict, Any, List

log = logging.getLogger("callback_scheduler")

IST = ZoneInfo("Asia/Kolkata")
TRAI_OPEN_TIME = time(9, 30)
TRAI_CLOSE_TIME = time(19, 0)

# Formal Callback States
CALLBACK_STATES = {
    "CALLBACK_REQUESTED",
    "CALLBACK_SCHEDULED",
    "CALLBACK_DUE",
    "CALLBACK_IN_PROGRESS",
    "CALLBACK_COMPLETED",
    "CALLBACK_RESCHEDULED",
    "CALLBACK_MISSED",
    "CALLBACK_CANCELLED",
}


def get_now_ist() -> datetime:
    return datetime.now(IST)


def utc_to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def is_in_business_hours(dt_ist: datetime) -> bool:
    """TRAI TCCCPR commercial calling window: 09:30-19:00 IST Mon-Sat (Sunday closed)."""
    if dt_ist.weekday() == 6:  # Sunday
        return False
    t = dt_ist.time()
    return TRAI_OPEN_TIME <= t <= TRAI_CLOSE_TIME


def next_business_hours_window(after_ist: datetime) -> datetime:
    """
    Finds the earliest valid business window opening at or strictly after after_ist.
    If after_ist is during open hours, returns after_ist.
    If after_ist is before 09:30 on Mon-Sat, returns 09:30 on the same day.
    If after_ist is after 19:00 or on Sunday, returns 09:30 on the next Mon-Sat.
    """
    cur = after_ist
    # If today is Sunday or after close time, advance to next day at open time
    if cur.weekday() == 6:  # Sunday
        next_day = cur.date() + timedelta(days=1)
        return datetime.combine(next_day, TRAI_OPEN_TIME, tzinfo=IST)
    
    if cur.time() < TRAI_OPEN_TIME:
        return datetime.combine(cur.date(), TRAI_OPEN_TIME, tzinfo=IST)
    elif cur.time() > TRAI_CLOSE_TIME:
        next_day = cur.date() + timedelta(days=1)
        if next_day.weekday() == 6:  # If next day is Sunday, advance to Monday
            next_day += timedelta(days=1)
        return datetime.combine(next_day, TRAI_OPEN_TIME, tzinfo=IST)
    
    return cur


def parse_callback_request(
    statement: str,
    base_time: datetime | None = None,
    tz_str: str = "Asia/Kolkata"
) -> Dict[str, Any]:
    """
    Parses a prospect's spoken or written callback statement into structured timing.
    Returns:
      {
        "is_callback": bool,
        "is_ambiguous": bool,
        "callback_confirmed": bool,
        "callback_date": str (YYYY-MM-DD),
        "callback_time": str (HH:MM),
        "callback_timezone": str,
        "window_start_utc": datetime | None,
        "window_end_utc": datetime | None,
        "deferred_for_business_hours": bool,
        "raw_statement": str,
        "clarification_needed": str | None,
      }
    """
    text = (statement or "").strip()
    low = text.lower()
    tz = ZoneInfo(tz_str)
    now_ist = (base_time.astimezone(tz) if base_time and base_time.tzinfo else
               datetime.now(tz) if not base_time else
               base_time.replace(tzinfo=timezone.utc).astimezone(tz))

    res = {
        "is_callback": False,
        "is_ambiguous": False,
        "callback_confirmed": False,
        "callback_date": None,
        "callback_time": None,
        "callback_timezone": tz_str,
        "window_start_utc": None,
        "window_end_utc": None,
        "deferred_for_business_hours": False,
        "raw_statement": text,
        "clarification_needed": None,
    }

    if not text:
        return res

    # Check for general callback intent
    callback_keywords = [
        "call", "callback", "ring", "phone", "talk", "baat", "contact", "reach",
        "tomorrow", "kal", "parso", "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday", "morning", "afternoon", "evening", "shaam",
        "dophar", "subah", "baje", "pm", "am", "next week", "later", "after"
    ]
    if not any(k in low for k in callback_keywords):
        return res

    res["is_callback"] = True

    # 1. Genuinely ambiguous statements with no parseable timeframe:
    ambiguous_patterns = [
        r"call\s+(?:me\s+)?(?:back\s+)?later\b",
        r"call\s+sometime\b",
        r"busy\s+right\s+now\b",
        r"cannot\s+talk\s+now\b",
        r"can't\s+talk\b",
        r"meeting\s+me\s+hoon\b",
        r"driving\b",
        r"baad\s+me\s+baat\b",
        r"baad\s+me\s+call\b",
        r"abhi\s+busy\s+hoon\b",
        r"call\s+when\s+i\s+return\b",
        r"return\s+to\s+(?:the\s+)?office\b",
    ]
    # Check if statement matches ambiguous pattern AND has no concrete day/time mention
    has_concrete_day = any(w in low for w in [
        "tomorrow", "kal", "parso", "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday", "1st", "2nd", "3rd", "4th", "5th", "6th",
        "7th", "8th", "9th", "10th", "11th", "12th", "13th", "14th", "15th",
        "16th", "17th", "18th", "19th", "20th", "21st", "22nd", "23rd", "24th",
        "25th", "26th", "27th", "28th", "29th", "30th", "31st", "next week", "next month"
    ])
    has_concrete_time = bool(re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|baje|o'?clock)\b", low))

    for pat in ambiguous_patterns:
        if re.search(pat, low) and not has_concrete_day and not has_concrete_time:
            res["is_ambiguous"] = True
            res["callback_confirmed"] = False
            res["clarification_needed"] = f"Ambiguous callback request: '{text}'. Clarify date and time."
            return res

    # 2. Extract Target Day
    target_date: Optional[date] = None

    # Check for ISO or DD-MM-YYYY format
    m_iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m_iso:
        try:
            target_date = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            pass

    m_dmy = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text)
    if not target_date and m_dmy:
        try:
            target_date = date(int(m_dmy.group(3)), int(m_dmy.group(2)), int(m_dmy.group(1)))
        except ValueError:
            pass

    # Check for relative offsets: "tomorrow", "kal"
    if not target_date:
        if re.search(r"\b(tomorrow|kal)\b", low):
            target_date = now_ist.date() + timedelta(days=1)
        elif re.search(r"\b(day after tomorrow|parso)\b", low):
            target_date = now_ist.date() + timedelta(days=2)
        elif re.search(r"\b(today|aaj)\b", low):
            target_date = now_ist.date()

    # Check for weekday names: "Wednesday", "next Monday"
    weekdays = {
        "monday": 0, "somwar": 0,
        "tuesday": 1, "mangalwar": 1,
        "wednesday": 2, "budhwar": 2,
        "thursday": 3, "guruwar": 3, "veervar": 3,
        "friday": 4, "shukrawar": 4,
        "saturday": 5, "shaniwar": 5,
        "sunday": 6, "ravivar": 6
    }
    if not target_date:
        for wname, wday in weekdays.items():
            if re.search(rf"\b{wname}\b", low):
                # Calculate next occurrence of this weekday
                days_ahead = (wday - now_ist.weekday()) % 7
                if days_ahead == 0 and "next" in low:
                    days_ahead = 7
                elif days_ahead == 0:
                    # If today is that day, depends on time
                    days_ahead = 7 if "next" in low else 0
                target_date = now_ist.date() + timedelta(days=days_ahead if days_ahead > 0 else 7)
                break

    # Check for "after the 15th", "after 15th", "on 15th"
    if not target_date:
        m_day_of_month = re.search(r"\b(?:after|on|post)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b", low)
        if m_day_of_month:
            dom = int(m_day_of_month.group(1))
            if 1 <= dom <= 31:
                year, month = now_ist.year, now_ist.month
                # If "after the 15th", target is the 16th
                if "after" in m_day_of_month.group(0):
                    dom += 1
                try:
                    if dom < now_ist.day:
                        # Moves to next month
                        month = month + 1 if month < 12 else 1
                        year = year + 1 if month == 1 else year
                    target_date = date(year, month, dom)
                except ValueError:
                    target_date = date(year, month + 1 if month < 12 else 1, 1)

    # Check for "next month", "contact me next month"
    if not target_date and re.search(r"\bnext\s+month\b", low):
        nxt_month = now_ist.month + 1 if now_ist.month < 12 else 1
        nxt_year = now_ist.year + 1 if nxt_month == 1 else now_ist.year
        target_date = date(nxt_year, nxt_month, 1)

    # Check for "in X days", "after X days"
    if not target_date:
        m_in_days = re.search(r"\b(?:in|after)\s+(\d+)\s+days?\b", low)
        if m_in_days:
            n_d = int(m_in_days.group(1))
            target_date = now_ist.date() + timedelta(days=max(1, min(n_d, 60)))

    # If no day detected, default to today if time is future, else tomorrow
    if not target_date:
        target_date = now_ist.date()

    # 3. Extract Clock Time
    target_time: Optional[time] = None

    # Exact time with am/pm or 24hr: e.g. "3 PM", "3:30 pm", "15:00", "4 baje"
    m_clock = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|o'?clock|hours?)?\b", low)
    if m_clock:
        hr = int(m_clock.group(1))
        mn = int(m_clock.group(2) or 0)
        suffix = (m_clock.group(3) or "").lower()

        # Check if clock pattern is valid
        is_clock_token = (
            suffix in ("am", "pm", "baje", "o'clock", "oclock") or
            m_clock.group(2) is not None or
            re.search(r"\b(?:at|around|by|after)\s+\d{1,2}\b", low)
        )

        if is_clock_token and hr <= 24:
            if suffix == "pm" and hr < 12:
                hr += 12
            elif suffix == "am" and hr == 12:
                hr = 0
            elif suffix in ("baje", "o'clock", "oclock") or not suffix:
                # Disambiguate morning vs evening for Hindi 'baje' or plain numbers
                if any(w in low for w in ("shaam", "evening", "raat", "night", "dophar", "afternoon")) and hr < 12:
                    hr += 12
                elif any(w in low for w in ("subah", "morning")) and hr == 12:
                    hr = 0
                elif hr < 9 and not suffix:
                    # 3 without am/pm in business context is almost always 3 PM (15:00)
                    hr += 12
            if 0 <= hr <= 23 and 0 <= mn <= 59:
                target_time = time(hr, mn)

    # Check for general time of day keywords: "morning", "afternoon", "evening"
    if not target_time:
        if re.search(r"\b(morning|subah)\b", low):
            target_time = time(10, 0)
        elif re.search(r"\b(afternoon|dophar)\b", low):
            target_time = time(14, 0)
        elif re.search(r"\b(evening|shaam)\b", low):
            target_time = time(17, 0)
        elif re.search(r"\bafter\s+5\s*(?:pm)?\b", low):
            target_time = time(17, 0)

    # If day was detected but no time at all:
    if target_date and not target_time:
        # Business standard for date-specified callback (e.g. "call next Monday", "after the 15th")
        # is morning business hours (10:00 AM IST)
        target_time = time(10, 0)

    # Construct the requested IST datetime
    req_dt_ist = datetime.combine(target_date, target_time, tzinfo=IST)

    # If requested time is already past today, advance date
    if req_dt_ist < now_ist and target_date == now_ist.date():
        target_date = target_date + timedelta(days=1)
        req_dt_ist = datetime.combine(target_date, target_time, tzinfo=IST)

    # 4. Enforce Business Hours Compliance (TRAI)
    # Never call earlier than prospect's requested time.
    # If requested time is outside business hours, defer to next legal window.
    effective_dt_ist = req_dt_ist
    deferred = False
    if not is_in_business_hours(req_dt_ist):
        effective_dt_ist = next_business_hours_window(req_dt_ist)
        deferred = True

    # 5. Build Final UTC Timestamps
    window_start_utc = ist_to_utc(effective_dt_ist)
    window_end_utc = window_start_utc + timedelta(hours=1)

    res["callback_confirmed"] = True
    res["callback_date"] = target_date.isoformat()
    res["callback_time"] = target_time.strftime("%H:%M")
    res["window_start_utc"] = window_start_utc
    res["window_end_utc"] = window_end_utc
    res["deferred_for_business_hours"] = deferred
    return res


def schedule_callback(
    db,
    lead_id: int,
    statement: str,
    source: str = "VOICE_CALL",
    assigned_channel: str = "CALL",
    assigned_owner: str = "AI_AGENT",
    transcript_ref: str = "",
    base_time: datetime | None = None
):
    """
    Schedules a persistent callback in the database.
    Survives PM2 restarts, VPS reboots, worker restarts.
    """
    from app.models.models import B2BLead, ScheduledCallback, WorkflowEvent

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} does not exist")

    parsed = parse_callback_request(statement, base_time=base_time)

    # Check for existing active callbacks for this lead and reschedule them
    active_callbacks = db.query(ScheduledCallback).filter(
        ScheduledCallback.lead_id == lead_id,
        ScheduledCallback.callback_status.in_(["CALLBACK_REQUESTED", "CALLBACK_SCHEDULED", "CALLBACK_DUE"])
    ).all()
    for cb in active_callbacks:
        cb.callback_status = "CALLBACK_RESCHEDULED"
        cb.updated_at = datetime.utcnow()

    # Create new callback record
    status = "CALLBACK_SCHEDULED" if parsed["callback_confirmed"] else "CALLBACK_REQUESTED"
    sc = ScheduledCallback(
        lead_id=lead_id,
        callback_requested=True,
        callback_date=parsed["callback_date"],
        callback_time=parsed["callback_time"],
        callback_timezone=parsed["callback_timezone"],
        callback_window_start=parsed["window_start_utc"],
        callback_window_end=parsed["window_end_utc"],
        callback_source=source,
        original_customer_statement=statement[:500],
        transcript_reference=transcript_ref[:100] if transcript_ref else None,
        callback_confirmed=parsed["callback_confirmed"],
        callback_status=status,
        assigned_channel=assigned_channel,
        assigned_owner=assigned_owner,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(sc)

    # Synchronize lead record
    lead.founder_callback_window = statement[:120]
    if parsed["callback_confirmed"]:
        lead.ai_retry_after = parsed["window_start_utc"]
        lead.status = "CALLBACK"
        lead.outreach_stage = "CALLBACK"
    else:
        # Ambiguous: hold automated calling, route to review
        lead.ai_retry_after = None

    # Immutable audit trail
    db.add(WorkflowEvent(
        lead_id=lead_id,
        event_type="CALLBACK_SCHEDULED" if parsed["callback_confirmed"] else "CALLBACK_REQUESTED_AMBIGUOUS",
        actor=source,
        channel=assigned_channel.lower(),
        payload={
            "statement": statement,
            "parsed": {
                "confirmed": parsed["callback_confirmed"],
                "date": parsed["callback_date"],
                "time": parsed["callback_time"],
                "window_start": parsed["window_start_utc"].isoformat() if parsed["window_start_utc"] else None,
                "deferred": parsed["deferred_for_business_hours"],
                "clarification": parsed["clarification_needed"],
            }
        },
        occurred_at=datetime.utcnow()
    ))

    db.commit()
    db.refresh(sc)
    return sc


def reschedule_callback(db, lead_id: int, new_statement: str, source: str = "VOICE_CALL"):
    """Reschedules an existing callback with a new customer timing statement."""
    return schedule_callback(db, lead_id, new_statement, source=source)


def cancel_callbacks_for_lead(db, lead_id: int, reason: str = "CANCELLED") -> int:
    """Cancels all pending callbacks for a lead (e.g. upon opt-out, purchase, or DNC)."""
    from app.models.models import ScheduledCallback, WorkflowEvent

    callbacks = db.query(ScheduledCallback).filter(
        ScheduledCallback.lead_id == lead_id,
        ScheduledCallback.callback_status.in_(["CALLBACK_REQUESTED", "CALLBACK_SCHEDULED", "CALLBACK_DUE", "CALLBACK_IN_PROGRESS"])
    ).all()

    count = len(callbacks)
    now = datetime.utcnow()
    for cb in callbacks:
        cb.callback_status = "CALLBACK_CANCELLED"
        cb.completed_at = now
        cb.updated_at = now

    if count > 0:
        db.add(WorkflowEvent(
            lead_id=lead_id,
            event_type="CALLBACK_CANCELLED",
            actor="SYSTEM",
            channel="call",
            payload={"reason": reason, "cancelled_count": count},
            occurred_at=now
        ))
        db.commit()
    return count


def claim_callback_atomic(db, callback_id: int, worker_id: str = "WORKER") -> Tuple[bool, Optional[Any]]:
    """
    Atomically claims a due callback for processing.
    Locks the lead to prevent duplicate concurrent processing across workers.
    """
    from app.models.models import ScheduledCallback, B2BLead
    from app.services.contact_coordinator import acquire_lead_lock

    cb = db.query(ScheduledCallback).filter(ScheduledCallback.id == callback_id).first()
    if not cb or cb.callback_status not in ("CALLBACK_SCHEDULED", "CALLBACK_DUE"):
        return False, None

    lead = db.query(B2BLead).filter(B2BLead.id == cb.lead_id).first()
    if not lead or not acquire_lead_lock(lead, db, actor=worker_id):
        return False, None

    cb.callback_status = "CALLBACK_IN_PROGRESS"
    cb.updated_at = datetime.utcnow()
    db.commit()
    return True, cb


def complete_callback(db, callback_id: int, outcome: str = "COMPLETED"):
    """Marks a callback as successfully completed and releases the lock."""
    from app.models.models import ScheduledCallback, B2BLead
    from app.services.contact_coordinator import release_lead_lock

    cb = db.query(ScheduledCallback).filter(ScheduledCallback.id == callback_id).first()
    if not cb:
        return None

    now = datetime.utcnow()
    cb.callback_status = "CALLBACK_COMPLETED"
    cb.completed_at = now
    cb.updated_at = now

    lead = db.query(B2BLead).filter(B2BLead.id == cb.lead_id).first()
    if lead:
        release_lead_lock(lead, db)

    db.commit()
    return cb


def mark_callback_missed(db, callback_id: int):
    """
    Records a missed callback (prospect did not answer scheduled call).
    Applies adaptive retry and preserves the agreed callback history.
    """
    from app.models.models import ScheduledCallback, B2BLead
    from app.services.contact_coordinator import release_lead_lock
    from app.services.followup_policy_engine import calculate_next_retry

    cb = db.query(ScheduledCallback).filter(ScheduledCallback.id == callback_id).first()
    if not cb:
        return None

    now = datetime.utcnow()
    cb.callback_status = "CALLBACK_MISSED"
    cb.completed_at = now
    cb.updated_at = now

    lead = db.query(B2BLead).filter(B2BLead.id == cb.lead_id).first()
    if lead:
        release_lead_lock(lead, db)
        current_attempts = (getattr(lead, "call_attempts", 0) or 0) + 1
        lead.call_attempts = current_attempts
        lead.last_call_date = now

        # Schedule sensible next retry
        may_retry, next_dt, reason = calculate_next_retry(current_attempts, "NO_ANSWER", base_time=now)
        lead.ai_retry_after = next_dt

    db.commit()
    return cb


def get_due_callbacks(db, now: datetime | None = None) -> List[Any]:
    """
    Returns all callbacks that are due to execute at or before now.
    Recovers missed jobs after server or process restarts.
    """
    from app.models.models import ScheduledCallback, B2BLead

    now_utc = now or datetime.utcnow()
    cbs = db.query(ScheduledCallback).join(B2BLead).filter(
        ScheduledCallback.callback_status.in_(["CALLBACK_SCHEDULED", "CALLBACK_DUE"]),
        ScheduledCallback.callback_window_start <= now_utc,
        ~B2BLead.status.in_(["DO_NOT_CONTACT", "OPTED_OUT", "PAID_CONVERSION", "WRONG_NUMBER"]),
        B2BLead.do_not_call == False
    ).order_by(ScheduledCallback.callback_window_start.asc()).all()

    return cbs
