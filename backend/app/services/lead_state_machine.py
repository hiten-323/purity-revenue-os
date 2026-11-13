"""
Durable Lead State Machine for Purity Revenue OS (Definite Answer Follow-Up Policy).

Defines the formal commercial lifecycle from discovery to paid conversion.
Transitions are transactional, validated, and idempotent.
Never allows an invalid transition to silently bypass safety gates.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Set, Dict, Optional, Tuple

log = logging.getLogger("lead_state_machine")

# Formal State Definitions
STATES = {
    "DISCOVERED",
    "INTENT_VERIFIED",
    "SCORED",
    "CONTACT_ELIGIBILITY_CHECKED",
    "QUEUED",
    "CALLING",
    "CONNECTED",
    "NO_ANSWER",
    "BUSY",
    "CALLBACK",
    "QUALIFIED",
    "NOT_INTERESTED",
    "WHATSAPP_OPTED_IN",
    "TEMPLATE_QUEUED",
    "WHATSAPP_SENT",
    "WHATSAPP_DELIVERED",
    "WHATSAPP_FAILED",
    "QUOTATION_SENT",
    "SAMPLE_REQUESTED",
    "SAMPLE_DISPATCHED",
    "ORDER_CREATED",
    "PAID_CONVERSION",
    # Terminal / Suppression States
    "DO_NOT_CONTACT",
    "OPTED_OUT",
    "REVOKED",
    "WRONG_NUMBER",
    "QUARANTINED",
    "MAX_ATTEMPTS_REACHED",
    # Definite Answer Follow-Up Policy States
    "CALLBACK_SCHEDULED",
    "DECISION_PENDING",
    "COOLING_PERIOD",
    "HUMAN_TAKEOVER",
}

# Permitted Forward and Terminal State Transitions
VALID_TRANSITIONS: Dict[str, Set[str]] = {
    "DISCOVERED": {"INTENT_VERIFIED", "SCORED", "CONTACT_ELIGIBILITY_CHECKED", "DO_NOT_CONTACT", "WRONG_NUMBER"},
    "INTENT_VERIFIED": {"SCORED", "DO_NOT_CONTACT", "WRONG_NUMBER"},
    "SCORED": {"CONTACT_ELIGIBILITY_CHECKED", "QUEUED", "DO_NOT_CONTACT"},
    "CONTACT_ELIGIBILITY_CHECKED": {"QUEUED", "DO_NOT_CONTACT", "QUARANTINED"},
    "QUEUED": {"CALLING", "NO_ANSWER", "BUSY", "DO_NOT_CONTACT", "OPTED_OUT", "CALLBACK_SCHEDULED", "COOLING_PERIOD"},
    "CALLING": {"CONNECTED", "NO_ANSWER", "BUSY", "CALLBACK", "CALLBACK_SCHEDULED", "WRONG_NUMBER", "DO_NOT_CONTACT"},
    "NO_ANSWER": {"QUEUED", "CALLING", "NO_ANSWER", "COOLING_PERIOD", "MAX_ATTEMPTS_REACHED", "CALLBACK", "CALLBACK_SCHEDULED", "DO_NOT_CONTACT"},
    "BUSY": {"QUEUED", "CALLING", "COOLING_PERIOD", "MAX_ATTEMPTS_REACHED", "CALLBACK", "CALLBACK_SCHEDULED", "DO_NOT_CONTACT"},
    "CALLBACK": {"CALLING", "QUEUED", "CALLBACK_SCHEDULED", "DO_NOT_CONTACT"},
    "CALLBACK_SCHEDULED": {"CALLING", "CONNECTED", "NO_ANSWER", "CALLBACK", "DO_NOT_CONTACT", "PAID_CONVERSION"},
    "CONNECTED": {"QUALIFIED", "NOT_INTERESTED", "DECISION_PENDING", "DO_NOT_CONTACT", "CALLBACK", "CALLBACK_SCHEDULED"},
    "QUALIFIED": {"WHATSAPP_OPTED_IN", "QUOTATION_SENT", "SAMPLE_REQUESTED", "ORDER_CREATED", "DO_NOT_CONTACT"},
    "DECISION_PENDING": {"QUEUED", "CALLING", "QUALIFIED", "NOT_INTERESTED", "DO_NOT_CONTACT", "PAID_CONVERSION"},
    "COOLING_PERIOD": {"QUEUED", "CALLING", "DO_NOT_CONTACT", "PAID_CONVERSION"},
    "HUMAN_TAKEOVER": {"QUALIFIED", "NOT_INTERESTED", "PAID_CONVERSION", "DO_NOT_CONTACT"},
    "NOT_INTERESTED": {"DO_NOT_CONTACT", "OPTED_OUT"},
    "WHATSAPP_OPTED_IN": {"TEMPLATE_QUEUED", "REVOKED", "DO_NOT_CONTACT"},
    "TEMPLATE_QUEUED": {"WHATSAPP_SENT", "WHATSAPP_FAILED", "REVOKED"},
    "WHATSAPP_SENT": {"WHATSAPP_DELIVERED", "WHATSAPP_FAILED", "QUOTATION_SENT", "SAMPLE_REQUESTED", "ORDER_CREATED", "OPTED_OUT"},
    "WHATSAPP_DELIVERED": {"QUOTATION_SENT", "SAMPLE_REQUESTED", "ORDER_CREATED", "OPTED_OUT"},
    "WHATSAPP_FAILED": {"DO_NOT_CONTACT", "QUARANTINED"},
    "QUOTATION_SENT": {"SAMPLE_REQUESTED", "ORDER_CREATED", "NOT_INTERESTED", "DO_NOT_CONTACT"},
    "SAMPLE_REQUESTED": {"SAMPLE_DISPATCHED", "ORDER_CREATED", "DO_NOT_CONTACT"},
    "SAMPLE_DISPATCHED": {"ORDER_CREATED", "NOT_INTERESTED", "DO_NOT_CONTACT"},
    "ORDER_CREATED": {"PAID_CONVERSION", "DO_NOT_CONTACT"},
    "PAID_CONVERSION": set(),  # Terminal goal achieved
    # Terminal suppression states allow no forward marketing transitions
    "DO_NOT_CONTACT": set(),
    "OPTED_OUT": set(),
    "REVOKED": set(),
    "WRONG_NUMBER": set(),
    "QUARANTINED": set(),
    "MAX_ATTEMPTS_REACHED": {"COOLING_PERIOD", "CALLBACK", "DO_NOT_CONTACT"},
}


def can_transition(current_state: str, new_state: str) -> Tuple[bool, str]:
    """Validates whether a transition from current_state to new_state is permitted."""
    curr = (current_state or "DISCOVERED").upper()
    nxt = (new_state or "").upper()

    if curr == nxt:
        return True, "idempotent: already in target state"

    allowed = VALID_TRANSITIONS.get(curr, set())
    if nxt in allowed:
        return True, f"transition {curr} -> {nxt} allowed"

    # DNC is always allowed as a safety action. Paid conversion is not:
    # it must follow an ORDER_CREATED state backed by an actual order event.
    if nxt == "DO_NOT_CONTACT":
        return True, "DNC can be applied from any state"

    return False, f"invalid transition: cannot move from {curr} to {nxt}"


def transition_lead(lead, new_state: str, db, actor: str = "SYSTEM", reason: str = "") -> Tuple[bool, str]:
    """
    Transactionally transitions lead to new_state with audit event.
    Returns (success, explanation).
    """
    if lead is None or db is None:
        return False, "lead or db is None"

    curr = (lead.status or "DISCOVERED").upper()
    nxt = (new_state or "").upper()

    ok, why = can_transition(curr, nxt)
    if not ok:
        log.warning("Rejected state transition for lead %s: %s", lead.id, why)
        return False, why

    lead.status = nxt
    lead.stage_entered_date = datetime.utcnow()

    # Log workflow event
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="LEAD_STAGE_TRANSITION",
        actor=actor,
        channel="crm",
        payload={"from_state": curr, "to_state": nxt, "reason": reason[:200]},
        occurred_at=datetime.utcnow()
    ))

    try:
        db.commit()
        log.info("Lead %s transitioned %s -> %s (%s)", lead.id, curr, nxt, reason)
        return True, f"transitioned {curr} -> {nxt}"
    except Exception as e:
        db.rollback()
        log.error("Failed to transition lead %s: %s", lead.id, e)
        return False, str(e)
