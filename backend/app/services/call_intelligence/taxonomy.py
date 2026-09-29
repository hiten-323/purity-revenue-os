"""Closed vocabularies for call results, and the maps between them.

Three vocabularies meet here and must not be confused:

* FSM outcome keys -- founder_call_pipeline.OUTCOMES (what the agent/engine
  reports and what moves outreach_stage). Unchanged by this module.
* Engine termination reasons -- what the telephony layer knows when the call
  ended WITHOUT the model deciding anything (no answer, busy, SIP error...).
* Result outcomes -- the enum stored on CallResult.outcome and used for
  learning/reporting.
"""
from __future__ import annotations

RESULT_OUTCOMES = (
    "NO_ANSWER", "BUSY", "VOICEMAIL", "FAILED", "WRONG_PERSON",
    "NOT_INTERESTED", "CALLBACK_REQUESTED", "INTERESTED", "SAMPLE_REQUESTED",
    "CATALOGUE_REQUESTED", "DO_NOT_CALL", "UNKNOWN",
)

# Outcomes that mean a person picked up and a conversation happened.
CONNECTED_OUTCOMES = frozenset({
    "WRONG_PERSON", "NOT_INTERESTED", "CALLBACK_REQUESTED", "INTERESTED",
    "SAMPLE_REQUESTED", "CATALOGUE_REQUESTED", "DO_NOT_CALL",
})
NOT_CONNECTED_OUTCOMES = frozenset({"NO_ANSWER", "BUSY", "VOICEMAIL", "FAILED"})
# "Interest" for learning: the lead asked for a next step.
POSITIVE_OUTCOMES = frozenset({
    "INTERESTED", "SAMPLE_REQUESTED", "CATALOGUE_REQUESTED", "CALLBACK_REQUESTED",
})
# A prior call ending in one of these means: do not dial this lead again.
STOP_OUTCOMES = frozenset({"NOT_INTERESTED", "WRONG_PERSON", "DO_NOT_CALL"})
# Founder follow-up list.
FOLLOW_UP_OUTCOMES = frozenset({
    "CALLBACK_REQUESTED", "INTERESTED", "SAMPLE_REQUESTED", "CATALOGUE_REQUESTED",
})

# FSM key (founder_call_pipeline.OUTCOMES) -> result outcome.
_FSM_TO_RESULT = {
    "WHATSAPP_OPT_IN": "CATALOGUE_REQUESTED",
    "SEND_INFO_EMAIL": "CATALOGUE_REQUESTED",
    "INTERESTED": "INTERESTED",
    "MEETING_REQUESTED": "INTERESTED",
    "CALLBACK_REQUESTED": "CALLBACK_REQUESTED",
    "HUMAN_HANDOFF": "CALLBACK_REQUESTED",
    "NOT_INTERESTED": "NOT_INTERESTED",
    "REJECTED": "NOT_INTERESTED",
    "WRONG_PERSON": "WRONG_PERSON",
    # The enum has no WRONG_NUMBER; the raw key is kept on raw_outcome.
    "WRONG_NUMBER": "WRONG_PERSON",
    "OPT_OUT": "DO_NOT_CALL",
    "NO_ANSWER": "NO_ANSWER",
    "WAITING": "NO_ANSWER",
    "HOLD": "NO_ANSWER",
    "TEMPORARILY_UNAVAILABLE": "NO_ANSWER",
    "BUSY": "BUSY",
    "VOICEMAIL": "VOICEMAIL",
    "FAILED": "FAILED",
    # A connected conversation the model could not classify.
    "OTHER": "UNKNOWN",
    # Result-enum values accepted verbatim (founder corrections, reconciler).
    "SAMPLE_REQUESTED": "SAMPLE_REQUESTED",
    "CATALOGUE_REQUESTED": "CATALOGUE_REQUESTED",
    "DO_NOT_CALL": "DO_NOT_CALL",
    "UNKNOWN": "UNKNOWN",
}


def result_outcome_for(key: str | None) -> str:
    return _FSM_TO_RESULT.get((key or "").strip().upper(), "UNKNOWN")


# Engine termination reasons -> the FSM key the pipeline understands.
# Every reason maps to a RETRYABLE, non-decision key: the telephony layer can
# say the call did not connect, never that the business said no.
ENGINE_TERMINATIONS = {
    "NO_ANSWER": "NO_ANSWER",
    "RING_TIMEOUT": "NO_ANSWER",
    "NOT_ANSWERED": "NO_ANSWER",
    "BUSY": "BUSY",
    "VOICEMAIL": "VOICEMAIL",
    "FAILED": "FAILED",
    "SIP_ERROR": "FAILED",
    "DISPATCH_ERROR": "FAILED",
    "AGENT_CRASH": "FAILED",
    "AGENT_ERROR": "FAILED",
    "TIMEOUT": "FAILED",
    "STUCK_DISPATCH": "NO_ANSWER",
    # Answered, but the session closed before the model recorded an outcome
    # (caller hung up mid-pitch, dropped line). The FSM gets FAILED -- the
    # same thing livekit-agent.js's Close handler already reports -- while
    # the CallResult keeps connected=True and whatever the transcript shows.
    "HANGUP_NO_OUTCOME": "FAILED",
    "COMPLETED_NO_OUTCOME": "FAILED",
}

# SIP final response codes -> termination reason.
_SIP = {
    486: "BUSY", 600: "BUSY",
    408: "NO_ANSWER", 480: "NO_ANSWER", 487: "NO_ANSWER",
    404: "SIP_ERROR", 484: "SIP_ERROR", 604: "SIP_ERROR", 410: "SIP_ERROR",
    403: "SIP_ERROR", 488: "SIP_ERROR", 500: "SIP_ERROR", 502: "SIP_ERROR",
    503: "SIP_ERROR", 504: "SIP_ERROR",
}


def termination_from_sip(code) -> str | None:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return None
    if c in _SIP:
        return _SIP[c]
    if 400 <= c < 700:
        return "SIP_ERROR"
    return None


# Scheduler dispositions written by the Nuraveda sidecar (CallAttempt /
# ScheduledCall.outcome) -> termination reason.
SIDECAR_DISPOSITIONS = {
    "no_answer": "NO_ANSWER",
    "busy": "BUSY",
    "failed": "FAILED",
    "dispatch_error": "DISPATCH_ERROR",
    "timeout": "TIMEOUT",
    "voicemail": "VOICEMAIL",
}


# lead.call_status written when a call ends. Nothing ever writes CALLING here.
def terminal_call_status_for(result_outcome: str, *, connected: bool | None = None,
                             reconciled: bool = False) -> str:
    out = (result_outcome or "UNKNOWN").upper()
    if out in NOT_CONNECTED_OUTCOMES:
        return out
    if out == "UNKNOWN":
        if reconciled and not connected:
            return "UNKNOWN_NO_RESULT"
        return "COMPLETED" if connected else "UNKNOWN_NO_RESULT"
    return "COMPLETED"


def hour_bucket(hour_local: int | None) -> str | None:
    if hour_local is None:
        return None
    h = int(hour_local)
    if h < 9:
        return "early"
    if h < 12:
        return "morning"
    if h < 15:
        return "midday"
    if h < 18:
        return "afternoon"
    if h < 21:
        return "evening"
    return "night"
