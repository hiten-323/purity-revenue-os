"""
Deliverability guard — protects the sending reputation of the founder's domain.

WHY THIS EXISTS
---------------
Sender reputation is a slow asset and a fast liability. purepantryprovisions.com
has sent roughly 50 emails in its entire history, so it has almost no positive
reputation banked, and the observed behaviour was exactly what gets a young
domain flagged:

  * 26 emails sent inside a single minute — no human sends like that, and to a
    receiving MTA it is indistinguishable from a spam blast.
  * a 56% failure rate (63 blocked of 113 attempts), every one of them a
    fabricated NXDOMAIN address.

The NXDOMAIN cases never left the building — domain_is_deliverable() catches
them at the SMTP chokepoint — but a failure rate that high means something
upstream is broken, and continuing to hammer through it is how a domain earns a
blocklisting. SPF, DKIM and DMARC are all correctly configured; none of them
protect against sending *behaviour*.

So this module gates volume and pace, and trips a breaker when quality drops.
It never silently drops a send: a blocked attempt returns a reason the founder
can read, and the queue retries later.

THRESHOLDS
----------
Deliberately conservative, and sized against reality rather than ambition:
there are 59 email-ready leads in the entire database, so a 40/day cap costs
nothing today while leaving room to raise it as reputation is earned.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Volume caps. A domain with no sending history should ramp, not blast.
MAX_PER_DAY = 40
MAX_PER_HOUR = 10

# Minimum gap between two sends. 26/minute was observed; 45s spacing makes the
# pattern look like a person working a list rather than a script.
MIN_SECONDS_BETWEEN_SENDS = 45

# Circuit breaker. Evaluated only once there is a meaningful sample, so a
# single early failure cannot halt everything.
BREAKER_MIN_SAMPLE = 15
BREAKER_FAILURE_RATE = 0.30


@dataclass
class SendVerdict:
    allowed: bool
    reason: str = ""
    retry_after_seconds: int = 0

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason,
                "retry_after_seconds": self.retry_after_seconds}


def _is_own_refusal(payload) -> bool:
    """
    Did WE decline to send, rather than a send actually failing?

    Gate A ("BLOCKED: … not verified") and Gate B ("HELD: …") stop a message
    before it reaches SMTP, so nothing was delivered, bounced, or seen by the
    provider. Counting those as failed attempts made the guard punish us for
    working: 15 refusals became a "100% failure rate" and halted sending on a
    day when no email had left the building at all.
    """
    # Read every key a caller might have used. This looked only at
    # "smtp_error" while the send paths write "error", so 31 refusals for a
    # missing ZOHO_APP_PASSWORD were counted as real delivery failures and
    # halted the breaker at "100% of the last 31 attempts failed" — on a run
    # where not one message had reached SMTP.
    p = payload or {}
    err = " ".join(str(p.get(k, "")) for k in
                   ("smtp_error", "error", "reason", "message")).lower()
    return (err.startswith("blocked:") or err.startswith("held:")
            or "blocked:" in err or "held:" in err
            or "is on file but not verified" in err or "email_verified=false" in err
            # A missing credential is a configuration fault, not a bad address.
            or "zoho_app_password" in err or "not set in .env" in err)


def _counts(db, since: datetime) -> tuple[int, int]:
    """
    (sent, failed) since a given time. `failed` counts real delivery failures
    only — our own gate refusals are excluded, per _is_own_refusal.
    """
    from app.models.models import WorkflowEvent
    rows = db.query(WorkflowEvent.event_type, WorkflowEvent.payload).filter(
        WorkflowEvent.event_type.in_(["EMAIL_SENT", "EMAIL_FAILED"]),
        WorkflowEvent.occurred_at >= since,
    ).all()
    sent = sum(1 for t, _ in rows if t == "EMAIL_SENT")
    failed = sum(1 for t, p in rows
                 if t == "EMAIL_FAILED" and not _is_own_refusal(p))
    return sent, failed


def _last_send_at(db) -> datetime | None:
    from app.models.models import WorkflowEvent
    row = (db.query(WorkflowEvent.occurred_at)
           .filter(WorkflowEvent.event_type == "EMAIL_SENT")
           .order_by(WorkflowEvent.occurred_at.desc()).first())
    return row[0] if row else None


# Provider-level blocks. These are refusals by our own mail provider about the
# ACCOUNT, not about one bad recipient — the account is throttled or suspended,
# and every further attempt digs the hole deeper. They must halt sending on
# sight, independent of the sample-size rule below.
#
# Observed live on 22 Jul: Zoho returned
#   550 5.4.6 Unusual sending activity detected ... usage-policy
# after the historic 26-emails-in-one-minute burst and 63 NXDOMAIN failures.
# Only one attempt had been made in the window, so the statistical breaker
# (which needs 15) stayed green and would have waved a whole batch through
# into a blocked account.
_PROVIDER_BLOCK_MARKERS = (
    "unusual sending activity", "usage-policy", "usage policy",
    "5.4.6", "rate limit", "too many messages", "sending limit",
    "account suspended", "blocked", "quota exceeded", "554 5.7.1",
)


def _recent_provider_block(db, since: datetime):
    """The most recent provider-level refusal, if any."""
    from app.models.models import WorkflowEvent
    rows = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "EMAIL_FAILED",
        WorkflowEvent.occurred_at >= since,
    ).order_by(WorkflowEvent.occurred_at.desc()).all()
    for e in rows:
        err = str((e.payload or {}).get("smtp_error", "")).lower()
        # NXDOMAIN is a bad ADDRESS, not a blocked account — never halt on it.
        if "nxdomain" in err or "does not exist" in err:
            continue
        # Our OWN refusals are not provider blocks. Gate A writes "BLOCKED: ...
        # is on file but not verified" and Gate B writes "HELD: ...", and
        # "blocked" is itself a provider-block marker — so the guard read our own
        # gate as Zoho suspending the account and locked sending for 24 hours. A
        # self-inflicted outage indistinguishable from a real one. Matching the
        # prefixes we write covers every internal refusal, not just one wording.
        if err.startswith("blocked:") or err.startswith("held:"):
            continue
        if "is on file but not verified" in err or "email_verified=false" in err:
            continue
        if any(m in err for m in _PROVIDER_BLOCK_MARKERS):
            return e, err
    return None, ""


def check_send_allowed(db) -> SendVerdict:
    """
    May we send another email right now? Called immediately before each send.

    Returns a verdict with a human-readable reason rather than raising, so the
    caller can record why a queued message was held instead of losing it.
    """
    now = datetime.utcnow()

    # ── Provider block: halt on sight, no sample size required ──
    blocked_ev, blocked_err = _recent_provider_block(db, now - timedelta(hours=24))
    if blocked_ev is not None:
        return SendVerdict(
            False,
            f"HALTED: the mail provider refused a send at "
            f"{blocked_ev.occurred_at:%d %b %H:%M} UTC — \"{blocked_err[:110]}\". "
            f"This is a block on the ACCOUNT, not one bad address. Resolve it "
            f"with the provider before sending again; retrying makes it worse.",
            retry_after_seconds=21600)

    # ── Circuit breaker: quality first ──
    day_sent, day_failed = _counts(db, now - timedelta(days=1))
    attempts = day_sent + day_failed
    if attempts >= BREAKER_MIN_SAMPLE:
        rate = day_failed / attempts
        if rate >= BREAKER_FAILURE_RATE:
            return SendVerdict(
                False,
                f"HALTED: {rate:.0%} of the last {attempts} attempts failed "
                f"(limit {BREAKER_FAILURE_RATE:.0%}). Sending is paused to protect "
                f"domain reputation — fix the bad addresses before resuming.",
                retry_after_seconds=3600)

    # ── Volume caps ──
    if day_sent >= MAX_PER_DAY:
        return SendVerdict(False,
                           f"daily cap reached ({day_sent}/{MAX_PER_DAY}) — resumes tomorrow",
                           retry_after_seconds=3600)
    hour_sent, _ = _counts(db, now - timedelta(hours=1))
    if hour_sent >= MAX_PER_HOUR:
        return SendVerdict(False,
                           f"hourly cap reached ({hour_sent}/{MAX_PER_HOUR})",
                           retry_after_seconds=600)

    # ── Pace ──
    last = _last_send_at(db)
    if last:
        gap = (now - last).total_seconds()
        if gap < MIN_SECONDS_BETWEEN_SENDS:
            wait = int(MIN_SECONDS_BETWEEN_SENDS - gap)
            return SendVerdict(False,
                               f"pacing: {wait}s until the next send "
                               f"(min {MIN_SECONDS_BETWEEN_SENDS}s apart)",
                               retry_after_seconds=wait)

    return SendVerdict(True, "within volume, pace and quality limits")


def health(db) -> dict:
    """Deliverability health for the dashboard. Reports real counts only."""
    now = datetime.utcnow()
    d_sent, d_failed = _counts(db, now - timedelta(days=1))
    w_sent, w_failed = _counts(db, now - timedelta(days=7))
    attempts = d_sent + d_failed
    rate = (d_failed / attempts) if attempts else 0.0
    verdict = check_send_allowed(db)
    return {
        "last_24h": {"sent": d_sent, "failed": d_failed,
                     "failure_rate": round(rate, 3)},
        "last_7d": {"sent": w_sent, "failed": w_failed},
        "limits": {"per_day": MAX_PER_DAY, "per_hour": MAX_PER_HOUR,
                   "min_seconds_between_sends": MIN_SECONDS_BETWEEN_SENDS,
                   "breaker_failure_rate": BREAKER_FAILURE_RATE,
                   "breaker_min_sample": BREAKER_MIN_SAMPLE},
        "can_send_now": verdict.allowed,
        "status_reason": verdict.reason,
        "capacity_remaining_today": max(0, MAX_PER_DAY - d_sent),
    }
