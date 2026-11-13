"""
AI Voice Calling Qualification & Adaptive Retry Engine for Purity Revenue OS.

Manages conversational qualification criteria and adaptive retry schedules under
the Definite Answer Follow-Up Policy:
- Supersedes the 3-attempt assumption: cold leads remain in pipeline.
- Integrates with FollowupPolicyEngine and CallbackScheduler.
- Honors prospect-stated callback times as highest priority.
- Never abandons prospects after 3 attempts; applies spaced retries and cooling periods.

Strict Non-Retry Conditions:
- Explicit refusal ("not interested")
- DNC request
- Opt-out or revoked consent
- SIP 400 or wrong number
- Confirmed purchase
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

from app.services.followup_policy_engine import (
    calculate_next_retry as _policy_calc_retry,
    apply_followup_disposition,
    TERMINAL_OUTCOMES,
    QUALIFIED_OUTCOMES,
)
from app.services.conversation_disposition import classify_conversation
from app.services.callback_scheduler import schedule_callback

log = logging.getLogger("ai_call_qualifier")


def calculate_next_retry(
    current_attempts: int,
    outcome: str,
    base_time: datetime | None = None
) -> Tuple[bool, Optional[datetime], str]:
    """
    Calculates adaptive retry window under the Definite Answer Follow-Up Policy.
    """
    return _policy_calc_retry(current_attempts, outcome, base_time=base_time)


def record_call_outcome(
    lead,
    outcome: str,
    transcript: str = "",
    summary: str = "",
    db=None,
    callback_statement: str = ""
) -> Dict[str, Any]:
    """
    Processes the outcome of an AI voice call, updating attempt counts,
    scheduling retries, scheduling callbacks, or marking terminal states.
    """
    if lead is None or db is None:
        return {"error": "lead or db is None"}

    now_utc = datetime.utcnow()
    lead.last_call_date = now_utc
    if summary:
        lead.call_summary = summary[:500]
    if transcript:
        lead.call_transcript = transcript[:2000]

    # Classify conversation disposition
    analysis = classify_conversation(
        transcript=transcript,
        outcome=outcome,
        summary=summary,
        channel="call"
    )
    disp = analysis["disposition"]

    # Handle Callback Request
    cb_text = callback_statement or (transcript if "call" in transcript.lower() else summary)
    if disp == "CALLBACK_REQUESTED" or "CALLBACK" in (outcome or "").upper():
        try:
            sc = schedule_callback(
                db,
                lead_id=lead.id,
                statement=cb_text,
                source="VOICE_CALL",
                assigned_channel="CALL",
                assigned_owner="AI_AGENT",
                transcript_ref=transcript[:100] if transcript else ""
            )
            return {
                "lead_id": lead.id,
                "disposition": "CALLBACK_REQUESTED",
                "callback_id": sc.id,
                "callback_status": sc.callback_status,
                "window_start": sc.callback_window_start.isoformat() if sc.callback_window_start else None,
                "confirmed": sc.callback_confirmed,
            }
        except Exception as e:
            log.error("Failed to schedule callback for lead %s: %s", lead.id, e)

    # Apply follow-up policy disposition
    res = apply_followup_disposition(
        lead,
        db,
        disposition=disp if disp != "CALLBACK_REQUESTED" else (outcome or "NO_ANSWER"),
        details={"transcript": transcript[:200], "summary": summary[:200]},
        now=now_utc
    )

    return {
        "lead_id": lead.id,
        "attempts": lead.call_attempts,
        "outcome": outcome,
        "disposition": disp,
        "may_retry": res.get("may_retry", False),
        "next_retry": res.get("next_retry"),
        "reason": res.get("reason", ""),
    }
