"""7-day per-domain email rule (29 Oct, Hiten).

At most one prospect email per company domain in any rolling 7 days.
Freemail addresses (gmail etc.) are keyed per address, not per domain.
Fails closed: any error means "do not send".
"""
from datetime import datetime, timedelta

WINDOW_DAYS = 7
FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "rediffmail.com",
    "icloud.com", "me.com", "aol.com", "zoho.com", "zohomail.in",
    "protonmail.com", "proton.me", "ymail.com",
}


def key_for(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return ""
    dom = e.rsplit("@", 1)[1]
    return e if dom in FREEMAIL else "@" + dom


def _matches(addr, key):
    return bool(addr) and key_for(addr) == key


def check(db, email, now=None):
    """Return (ok, why). ok=False means hold this send."""
    try:
        key = key_for(email)
        if not key:
            return False, "DOMAIN RULE: no valid address"
        now = now or datetime.utcnow()
        cutoff = now - timedelta(days=WINDOW_DAYS)
        from app.models.models import WorkflowEvent, EmailDraft
        for ev in db.query(WorkflowEvent).filter(
                WorkflowEvent.event_type == "EMAIL_SENT",
                WorkflowEvent.occurred_at >= cutoff).all():
            p = ev.payload if isinstance(ev.payload, dict) else {}
            if _matches(p.get("to"), key):
                return False, f"DOMAIN RULE: {key} already emailed in last {WINDOW_DAYS} days"
        for d in db.query(EmailDraft).filter(
                EmailDraft.status == "SENT",
                EmailDraft.sent_at >= cutoff).all():
            if _matches(d.recipient, key):
                return False, f"DOMAIN RULE: {key} already emailed in last {WINDOW_DAYS} days"
        return True, ""
    except Exception as exc:  # fail closed
        return False, f"DOMAIN RULE unavailable ({exc.__class__.__name__}); not sent"
