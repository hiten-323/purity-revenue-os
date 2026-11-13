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
    # The call never rang the business: the agent never joined the room, the
    # trunk refused the INVITE before ringing, or the provider ran out of
    # credit. Not an attempt on the lead; never a no-answer.
    "DISPATCH_FAILED",
    # 22 Oct: answered, hung up within 1-3 turns (0-1 caller turns), no decision.
    "CALLEE_HUNGUP",
    # 22 Oct: answered, 4+ turns, ended without a recorded outcome -> founder review.
    "INCOMPLETE",
)

# Outcomes that mean a person picked up and a conversation happened.
CONNECTED_OUTCOMES = frozenset({
    "WRONG_PERSON", "NOT_INTERESTED", "CALLBACK_REQUESTED", "INTERESTED",
    "SAMPLE_REQUESTED", "CATALOGUE_REQUESTED", "DO_NOT_CALL",
    "CALLEE_HUNGUP", "INCOMPLETE",
})
NOT_CONNECTED_OUTCOMES = frozenset({"NO_ANSWER", "BUSY", "VOICEMAIL", "FAILED", "DISPATCH_FAILED"})
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
    "CALLEE_HUNGUP": "CALLEE_HUNGUP",  # 22 Oct: own outcome (was UNKNOWN)
    "INCOMPLETE": "INCOMPLETE",
    "NOT_ANSWERED": "NO_ANSWER",
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
    "DISPATCH_FAILED": "DISPATCH_FAILED",
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
    # Never rang (see NEVER_RANG_TERMINATIONS). The FSM key is only used for
    # validation; the status endpoint releases the attempt instead of
    # recording an FSM outcome for these.
    "DISPATCH_FAILED": "FAILED",
    "AGENT_NOT_JOINED": "FAILED",
    "PRE_RING_FAILURE": "FAILED",
    "CREDITS_EXHAUSTED": "FAILED",
}

# Terminations where the business's phone never rang. These do not count as
# an attempt on the lead (the attempt is released) and are labelled
# DISPATCH_FAILED, never NO_ANSWER.
NEVER_RANG_TERMINATIONS = frozenset({
    "DISPATCH_FAILED", "AGENT_NOT_JOINED", "PRE_RING_FAILURE", "CREDITS_EXHAUSTED",
})

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
    "dispatch_failed": "DISPATCH_FAILED",
    "credits_exhausted": "CREDITS_EXHAUSTED",
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

# 21 Oct (founder-approved): one exact final label per finished call, derived
# from the existing result outcome + termination + call details above (not a
# second taxonomy). Score 0-1; below LABEL_REVIEW_BELOW goes to founder review.
FINAL_LABELS = (
    "NO_ANSWER", "BUSY", "WRONG_NUMBER", "GATEKEEPER", "CALLBACK_REQUESTED",
    "NOT_INTERESTED", "ALREADY_HAS_SUPPLIER", "SEND_CATALOGUE_WHATSAPP",
    "SEND_CATALOGUE_EMAIL", "PRICE_ENQUIRY", "SAMPLE_REQUEST", "HOT_DEAL",
    "HUNG_UP_EARLY", "AGENT_ERROR", "INCOMPLETE",
)
LABEL_REVIEW_BELOW = 0.6
_CONF_SCORE = {"OBSERVED": 0.9, "HUMAN_CORRECTED": 1.0, "INFERRED": 0.65, "LOW_CONFIDENCE": 0.35}


def final_label(*, outcome, raw_outcome=None, termination=None, connected=None,
                objections=None, confidence=None, details=None, interest=None):
    o = (outcome or "").upper(); raw = (raw_outcome or "").upper()
    term = (termination or "").upper(); d = details or {}
    obj = [str(x).upper() for x in (objections or [])]
    base = _CONF_SCORE.get(confidence or "", 0.5)
    chan = str(d.get("preferred_channel") or "").upper()
    if raw == "CALLEE_HUNGUP" or o == "CALLEE_HUNGUP":
        return "HUNG_UP_EARLY", 0.8
    if o == "INCOMPLETE":
        return "INCOMPLETE", 0.5
    if o == "DISPATCH_FAILED" or term in NEVER_RANG_TERMINATIONS:
        return "AGENT_ERROR", 0.9
    if o == "BUSY":
        return "BUSY", 0.95
    if o in ("NO_ANSWER", "VOICEMAIL"):
        return "NO_ANSWER", 0.95
    if o == "WRONG_PERSON":
        return ("WRONG_NUMBER" if raw == "WRONG_NUMBER" else "GATEKEEPER"), base
    if o == "DO_NOT_CALL":
        return "NOT_INTERESTED", max(base, 0.9)
    if o == "NOT_INTERESTED":
        if "EXISTING_SUPPLIER" in obj or str(d.get("objection") or "").upper() == "EXISTING_SUPPLIER":
            return "ALREADY_HAS_SUPPLIER", base
        return "NOT_INTERESTED", base
    if o == "CALLBACK_REQUESTED":
        return "CALLBACK_REQUESTED", base
    if o == "SAMPLE_REQUESTED":
        return "SAMPLE_REQUEST", base
    if o == "CATALOGUE_REQUESTED":
        if raw == "WHATSAPP_OPT_IN" or chan == "WHATSAPP":
            return "SEND_CATALOGUE_WHATSAPP", base
        return "SEND_CATALOGUE_EMAIL", base
    if o == "INTERESTED":
        if raw in ("MEETING_REQUESTED", "HUMAN_HANDOFF") or str(interest or "").upper() == "HIGH":
            return "HOT_DEAL", base
        if "PRICE" in obj or str(d.get("objection") or "").upper() == "PRICE":
            return "PRICE_ENQUIRY", base
        return "HOT_DEAL", min(base, 0.55)
    if o == "FAILED" and not connected:
        return "NO_ANSWER", 0.8
    if connected:
        return "HUNG_UP_EARLY", (0.7 if term in ("HANGUP_NO_OUTCOME", "COMPLETED_NO_OUTCOME") else 0.4)
    return "AGENT_ERROR", 0.3
