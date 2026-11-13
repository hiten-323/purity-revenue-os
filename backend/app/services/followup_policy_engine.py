"""
Adaptive Retry & Follow-Up Policy Engine for Purity Revenue OS.

Implements the Definite Answer Follow-Up Policy:
- Supersedes the 3-attempt lifetime termination rule.
- A cold lead remains in the sales pipeline until a definite disposition is obtained.
- Adaptive retry schedule:
    - Attempt 1: Initial eligible window
    - Attempt 2: 24-48 hours later (default 24h)
    - Attempt 3: 3-5 days later (default 72h)
    - Attempt 4: 5 days later (120h)
    - Attempt 5: 7 days later (168h)
    - Subsequent / cycle ceiling: Transitions to COOLING_PERIOD (21 days),
      never permanently discarded.
- Terminal Stops:
    - Explicit refusal / Not interested
    - DNC request
    - Channel opt-out
    - Wrong number / invalid number
    - Purchase completed (paid conversion)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any

log = logging.getLogger("followup_policy_engine")

# Adaptive Retry Delay Matrix (Hours)
RETRY_DELAYS = {
    1: 24,       # Attempt 1 -> Attempt 2: 24 hours
    2: 72,       # Attempt 2 -> Attempt 3: 3 days (72h)
    3: 120,      # Attempt 3 -> Attempt 4: 5 days (120h)
    4: 168,      # Attempt 4 -> Attempt 5: 7 days (168h)
}
MAX_CYCLE_ATTEMPTS = 5
COOLING_PERIOD_DAYS = 21

TERMINAL_OUTCOMES = frozenset([
    "DNC", "DO_NOT_CONTACT", "REFUSED", "NOT_INTERESTED", "OPT_OUT",
    "OPTED_OUT", "WRONG_NUMBER", "INVALID_NUMBER", "PURCHASED", "PAID_CONVERSION"
])

QUALIFIED_OUTCOMES = frozenset([
    "QUALIFIED", "WHATSAPP_OPT_IN", "CATALOGUE_REQUESTED", "SAMPLE_REQUESTED",
    "QUOTATION_REQUESTED", "ORDER_CREATED"
])


def calculate_next_retry(
    current_attempts: int,
    outcome: str,
    base_time: datetime | None = None
) -> Tuple[bool, Optional[datetime], str]:
    """
    Calculates whether a lead may be retried and at what timestamp.
    Returns (may_retry, next_retry_dt, reason).
    """
    now = base_time or datetime.utcnow()
    out = (outcome or "").upper().strip()

    # 1. Terminal stops: non-retryable
    if out in TERMINAL_OUTCOMES:
        return False, None, f"terminal outcome {out}: non-retryable"

    # 2. Positive qualification stops: cold phase done, moved to conversion
    if out in QUALIFIED_OUTCOMES:
        return False, None, f"qualification outcome {out}: cold outreach completed"

    # 3. Callback requested: handled by callback_scheduler
    if out == "CALLBACK_REQUESTED":
        return True, None, "callback requested: timing managed by callback_scheduler"

    # 4. Decision pending: spaced follow-up
    if out == "DECISION_PENDING":
        next_dt = now + timedelta(days=7)
        return True, next_dt, "decision pending: follow-up scheduled after 7 days"

    # 5. Unanswered attempts retry schedule
    if out in ("NO_ANSWER", "VOICEMAIL", "BUSY", "CALL_FAILED", "UNANSWERED", "MISSED", "HUNG_UP_EARLY"):
        if current_attempts >= MAX_CYCLE_ATTEMPTS:
            cooling_end = now + timedelta(days=COOLING_PERIOD_DAYS)
            return True, cooling_end, f"cooling period entered: {current_attempts} unanswered attempts; cooling for {COOLING_PERIOD_DAYS} days"

        delay_hours = RETRY_DELAYS.get(current_attempts, 168)
        retry_dt = now + timedelta(hours=delay_hours)
        return True, retry_dt, f"adaptive retry {current_attempts + 1} scheduled after {delay_hours}h"

    return False, None, f"unsupported outcome {out}"


def apply_followup_disposition(
    lead,
    db,
    disposition: str,
    details: Dict[str, Any] | None = None,
    now: datetime | None = None
) -> Dict[str, Any]:
    """
    Applies structured disposition to a lead according to the Definite Answer Follow-Up Policy.
    """
    from app.services.lead_state_machine import transition_lead
    from app.services.callback_scheduler import cancel_callbacks_for_lead
    from app.models.models import WorkflowEvent

    now_utc = now or datetime.utcnow()
    disp = (disposition or "").upper().strip()
    details = details or {}

    current_attempts = getattr(lead, "call_attempts", 0) or 0
    if disp in ("NO_ANSWER", "BUSY", "VOICEMAIL", "UNANSWERED", "CALL_FAILED"):
        current_attempts += 1
        lead.call_attempts = current_attempts
        lead.last_call_date = now_utc

    may_retry, retry_dt, reason = calculate_next_retry(current_attempts, disp, base_time=now_utc)
    lead.ai_retry_after = retry_dt

    event_payload = {
        "disposition": disp,
        "attempts": current_attempts,
        "may_retry": may_retry,
        "next_retry": retry_dt.isoformat() if retry_dt else None,
        "reason": reason,
        "details": details,
    }

    # Handle state transitions based on disposition
    if disp in ("DNC", "DO_NOT_CONTACT", "OPT_OUT"):
        lead.do_not_call = True
        lead.contact_status = "DO_NOT_CONTACT"
        cancel_callbacks_for_lead(db, lead.id, reason="PROSPECT_OPT_OUT")
        transition_lead(lead, "DO_NOT_CONTACT", db, actor="FOLLOWUP_ENGINE", reason="Explicit DNC / Opt-Out")

    elif disp in ("NOT_INTERESTED", "REFUSED"):
        transition_lead(lead, "NOT_INTERESTED", db, actor="FOLLOWUP_ENGINE", reason="Prospect stated not interested")

    elif disp in ("WRONG_NUMBER", "INVALID_NUMBER"):
        transition_lead(lead, "WRONG_NUMBER", db, actor="FOLLOWUP_ENGINE", reason="Wrong or invalid contact endpoint")

    elif disp == "DECISION_PENDING":
        transition_lead(lead, "DECISION_PENDING", db, actor="FOLLOWUP_ENGINE", reason=reason)

    elif disp in ("QUALIFIED", "POSITIVE", "WHATSAPP_OPT_IN", "CATALOGUE_REQUESTED", "SAMPLE_REQUESTED"):
        transition_lead(lead, "QUALIFIED", db, actor="FOLLOWUP_ENGINE", reason="Positive response / requirement qualified")

    elif disp in ("NO_ANSWER", "BUSY", "VOICEMAIL", "UNANSWERED", "CALL_FAILED", "MISSED"):
        if current_attempts >= MAX_CYCLE_ATTEMPTS:
            transition_lead(lead, "COOLING_PERIOD", db, actor="FOLLOWUP_ENGINE", reason=reason)
        else:
            transition_lead(lead, "NO_ANSWER", db, actor="FOLLOWUP_ENGINE", reason=reason)

    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="FOLLOWUP_DISPOSITION_APPLIED",
        actor="FOLLOWUP_ENGINE",
        channel="omnichannel",
        payload=event_payload,
        occurred_at=now_utc
    ))

    db.commit()
    return event_payload
