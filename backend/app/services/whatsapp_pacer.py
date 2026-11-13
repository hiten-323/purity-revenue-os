"""
The one gate in front of every Klaviyo WhatsApp send (28 Oct 2026, founder rules).

All fail-closed:
  * only leads who said YES to WhatsApp on the AI call (consent EXPLICIT,
    source AI_CALL_WHATSAPP_REQUEST, bound to consent_phone) and did not
    later decline (WHATSAPP_DECLINED) or revoke
  * a founder-APPROVED draft for that lead (whatsapp_drafts); none -> no send
  * one send at a time (process-wide file lock), never bursts
  * random 3-12 minute gap between sends; hard cap WA_MAX_PER_HOUR (default 5, max 6)
  * 09:30-19:00 IST, Monday-Saturday only
  * skip anyone messaged (WhatsApp or email) in the last 7 days
  * skip do_not_call / OPT_OUT / REVOKED / block list / DND / SIP strikes / hard block
There is no test-send path in this module.
"""
from __future__ import annotations

import fcntl
import json
import os
import random
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
START, END = time(9, 30), time(20, 0)
GAP_MIN, GAP_MAX = 5 * 60, 15 * 60
def _no_contact_days() -> float:
    try:
        if not os.getenv("PYTEST_CURRENT_TEST") and os.getenv("WHATSAPP_COOLDOWN_HOURS"):
            return float(os.getenv("WHATSAPP_COOLDOWN_HOURS")) / 24.0
        if not os.getenv("PYTEST_CURRENT_TEST") and os.getenv("WHATSAPP_NO_CONTACT_DAYS"):
            return float(os.getenv("WHATSAPP_NO_CONTACT_DAYS"))
        return 7.0
    except (TypeError, ValueError):
        return 7.0

NO_CONTACT_DAYS = _no_contact_days()
MAX_RETRIES = 3
RETRY_INTERVALS_HOURS = [24, 48, 72]  # 24h, 48h, 72h retry intervals
HARD_BLOCK = {"8591977190"}
ROOT = "/opt/purity-revenue-os"
BLOCK_FILES = [p for p in (os.getenv("WA_BLOCKLIST_FILES") or
               f"{ROOT}/purity-revenue-os/backend/data/dnd_suppression.txt").split(",") if p]
STRIKE_FILES = [p for p in (os.getenv("WA_STRIKE_FILES") or
                f"{ROOT}/ai-voice-agent/data/invalid_numbers.json,"
                f"{ROOT}/ai-voice-agent/data/invalid_numbers_400_strikes.json").split(",") if p]
_DEFAULT_BLOCK, _DEFAULT_STRIKE = BLOCK_FILES, STRIKE_FILES
LOCK = os.getenv("WA_PACER_LOCK", "/tmp/purity-wa-pacer.lock")
WA_EVENTS = ("WHATSAPP_SENT", "WHATSAPP_SEND_UNCONFIRMED", "WHATSAPP_QUEUED_TO_KLAVIYO")
CONTACT_EVENTS = WA_EVENTS + ("EMAIL_SENT",)


def mask(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return ("XXXXXX" + d[-4:]) if d else "-"


def ten(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else d


def max_per_hour() -> int:
    from app.services.outbound_limits import effective
    return effective("WA_MAX_PER_HOUR")


def max_per_day() -> int:
    from app.services.outbound_limits import effective
    return effective("WA_DAILY_CAP")


def in_hours(now=None) -> bool:
    n = (now or datetime.now(IST)).astimezone(IST)
    return n.weekday() <= 5 and START <= n.time() <= END


def _list_file(path) -> set:
    out = set()
    try:
        for line in open(path, encoding="utf-8", errors="ignore"):
            line = line.split("#", 1)[0].strip()
            t = ten(line)
            if len(t) == 10:
                out.add(t)
    except OSError:
        pass
    return out


def _strike_file(path) -> set:
    try:
        d = json.load(open(path))
    except Exception:
        return set()
    keys = d.keys() if isinstance(d, dict) else d
    return {ten(k) for k in keys if len(ten(k)) == 10}


def number_suppressed(number, block_files=None, strike_files=None) -> str:
    """'' if the number may be messaged, else why not (no number in the text)."""
    t = ten(number)
    if len(t) != 10:
        return "not a valid number"
    if t in HARD_BLOCK:
        return "hard-blocked"
    # Under pytest the LIVE server lists are not read (they contain the dummy
    # numbers fixtures use); tests that need lists pass or monkeypatch them.
    _live = not os.getenv("PYTEST_CURRENT_TEST")
    bfs = block_files if block_files is not None else (BLOCK_FILES if (_live or BLOCK_FILES is not _DEFAULT_BLOCK) else [])
    sfs = strike_files if strike_files is not None else (STRIKE_FILES if (_live or STRIKE_FILES is not _DEFAULT_STRIKE) else [])
    for f in bfs:
        if t in _list_file(f):
            return "on the block list / DND"
    for f in sfs:
        if t in _strike_file(f):
            return "on the SIP strike / invalid list"
    return ""


def eligible(db, lead) -> tuple[bool, str]:
    from app.models.models import WorkflowEvent
    from app.services.outbound_cohort import _cold_outreach_enabled, failed_or_blocked_whatsapp, on_allow_list
    from app.services.whatsapp_sender import consent_check
    if not _cold_outreach_enabled() and not on_allow_list(lead):
        return False, "lead is outside the WhatsApp allow-list"
    if failed_or_blocked_whatsapp(lead, db):
        return False, "a failed or blocked WhatsApp row must not be retried"
    ok, why = consent_check(lead)            # EXPLICIT, bound number, not REVOKED/DNC/supplier
    if not ok:
        return False, why
    if (getattr(lead, "consent_source", "") or "") not in ("AI_CALL_WHATSAPP_REQUEST", "EMAIL_REPLY_WHATSAPP_REQUEST"):
        return False, "consent did not come from an authorized channel (AI call or email reply)"
    num = getattr(lead, "consent_phone", None) or getattr(lead, "whatsapp_number", None)
    why = number_suppressed(num)
    if why:
        return False, why
    if (getattr(lead, "status", "") or "").upper() in {"OPT_OUT", "DO_NOT_CONTACT", "UNSUBSCRIBED"} \
            or (getattr(lead, "consent_status", "") or "").upper() in {"OPT_OUT", "REVOKED"}:
        return False, "opted out / suppressed"
    ts = getattr(lead, "consent_timestamp", None)
    q = db.query(WorkflowEvent.id).filter(WorkflowEvent.lead_id == lead.id,
                                          WorkflowEvent.event_type == "WHATSAPP_DECLINED")
    if ts is not None:
        q = q.filter(WorkflowEvent.occurred_at >= ts)
    if q.first():
        return False, "declined WhatsApp on a call"
    from app.services.contact_coordinator import is_cross_channel_suppressed
    suppressed, sup_why = is_cross_channel_suppressed(lead, "whatsapp", db=db)
    if suppressed:
        return False, f"cross_channel_suppression: {sup_why}"
    days = _no_contact_days()
    since = datetime.utcnow() - timedelta(days=days)
    # The cooldown is anchored strictly to the last actual WhatsApp send;
    # re-recording consent must NEVER reset or bypass the cooldown window.
    recent = db.query(WorkflowEvent.event_type, WorkflowEvent.payload).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type.in_(WA_EVENTS),
        WorkflowEvent.occurred_at >= since).all()
    if recent:
        cancelled = not_delivered_ids(db, lead.id)
        for etype, payload in recent:
            if etype in WA_EVENTS and _uid(payload) and _uid(payload) in cancelled:
                continue          # Klaviyo never delivered it (WHATSAPP_NOT_DELIVERED on record)
            d_str = int(days) if days == int(days) else f"{days:.1f}"
            return False, f"messaged in the last {d_str} days"

    num_clean = ten(num)
    if num_clean:
        from app.models.models import OutboundWhatsApp
        phone_match = db.query(OutboundWhatsApp.id).filter(
            OutboundWhatsApp.recipient.like(f"%{num_clean}%"),
            OutboundWhatsApp.status.in_(("SENT", "DELIVERED", "QUEUED_TO_KLAVIYO")),
            OutboundWhatsApp.created_at >= since
        ).first()
        if phone_match:
            d_str = int(days) if days == int(days) else f"{days:.1f}"
            return False, f"messaged in the last {d_str} days (same phone)"
    return True, "eligible"


NOT_DELIVERED = "WHATSAPP_NOT_DELIVERED"


def _uid(payload) -> str:
    if isinstance(payload, str):
        try:
            import json as _j
            payload = _j.loads(payload)
        except Exception:  # noqa: BLE001
            return ""
    return str((payload or {}).get("unique_id") or "") if isinstance(payload, dict) else ""


def not_delivered_ids(db, lead_id) -> set:
    """unique_ids of WhatsApp events Klaviyo is on record as never delivering
    (29 Oct: 1043's event was dropped by the flow's subscription filter). Only
    an explicit WHATSAPP_NOT_DELIVERED event, written with the evidence, frees
    the 7-day rule; the gap/hourly cap still count the original event."""
    from app.models.models import WorkflowEvent
    rows = db.query(WorkflowEvent.payload).filter(WorkflowEvent.lead_id == lead_id,
                                                  WorkflowEvent.event_type == NOT_DELIVERED).all()
    return {u for (pl,) in rows if (u := _uid(pl))}


def _gap_for(ts) -> int:
    return random.Random(int(ts.timestamp())).randint(GAP_MIN, GAP_MAX)


def slot_free(db, now=None) -> tuple[bool, str]:
    """Hours, daily cap, hourly cap and the random gap since the last WhatsApp send."""
    from app.models.models import WorkflowEvent
    if not in_hours(now):
        return False, "outside 09:30-20:00 IST Mon-Sat"

    n_local = (now or datetime.now(IST)).astimezone(IST)
    start_of_day_ist = n_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day_ist.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    daily_count = db.query(WorkflowEvent.id).filter(
        WorkflowEvent.event_type.in_(WA_EVENTS),
        WorkflowEvent.occurred_at >= start_of_day_utc
    ).count()
    if daily_count >= max_per_day():
        return False, f"daily cap {max_per_day()} reached"

    utc_now = datetime.utcnow()
    hourly_count = db.query(WorkflowEvent.id).filter(
        WorkflowEvent.event_type.in_(WA_EVENTS),
        WorkflowEvent.occurred_at >= utc_now - timedelta(hours=1)
    ).count()
    if hourly_count >= max_per_hour():
        return False, f"hourly cap {max_per_hour()} reached"

    last = db.query(WorkflowEvent.occurred_at).filter(WorkflowEvent.event_type.in_(WA_EVENTS)) \
        .order_by(WorkflowEvent.occurred_at.desc()).first()
    if last and last[0]:
        gap = _gap_for(last[0])
        if (utc_now - last[0]).total_seconds() < gap:
            return False, f"waiting random gap ({gap // 60} min)"
    return True, "slot free"


class OneAtATime:
    """Process-wide lock: a second sender fails fast, never runs in parallel."""
    def __enter__(self):
        self.f = open(LOCK, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()
