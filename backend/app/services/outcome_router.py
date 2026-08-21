"""Maps an observed buyer intent to the next outreach *shape*.

Not a second decision engine. evaluate_next_action() still owns permission
(suppression, trust, account cap, sequence). This table only answers:
given what they said, what kind of next step is appropriate?

Automatic means ordinary warm email may send. It does not mean:
  - invent a catalogue URL
  - ship samples without founder
  - quote prices
  - book meetings
  - treat an out-of-office as a human yes
  - WhatsApp a scraped number
"""
from __future__ import annotations

# action, execute, reason
OUTCOMES: dict[str, tuple[str, bool, str]] = {
    "OPTED_OUT": ("COOLDOWN", False, "buyer asked to stop"),
    "NOT_INTERESTED": ("COOLDOWN", False, "buyer declined"),
    "DO_NOT_CONTACT": ("COOLDOWN", False, "do-not-contact"),
    "COMPLAINT": ("COOLDOWN", False, "complaint — stop"),
    "BOUNCED": ("COOLDOWN", False, "hard bounce — address dead"),
    "OUT_OF_OFFICE": ("WAIT", False, "machine OOO — cadence continues, not a reply"),
    "MACHINE_REPLY": ("WAIT", False, "auto-responder — not a human"),
    "CATALOGUE_REQUESTED": ("SEND_CATALOGUE", True, "explicit catalogue request"),
    "PRICING_REQUESTED": ("FOUNDER_REVIEW", False, "commercial quote needs founder"),
    "NEGOTIATION": ("FOUNDER_REVIEW", False, "negotiation needs founder"),
    "SAMPLE_REQUESTED": ("FOUNDER_REVIEW", False, "sample dispatch needs founder"),
    "MEETING_REQUESTED": ("FOUNDER_REVIEW", False, "meeting needs founder calendar"),
    "CALLBACK": ("FOUNDER_REVIEW", False, "callback needs founder"),
    "WRONG_PERSON": ("FOUNDER_REVIEW", False, "wrong contact — founder to redirect"),
    "EXISTING_SUPPLIER": ("NURTURE", False, "already supplied — long nurture, no blast"),
    "CALL_LATER": ("WAIT", False, "asked to wait — do not send now"),
    "INTERESTED": ("CONTINUE_CONVERSATION", False, "human replied — no automatic re-send"),
    "NONE": ("CONTINUE", False, "no new intent"),
}


def next_shape(intent: str | None) -> dict:
    action, execute, reason = OUTCOMES.get(
        (intent or "NONE").upper(),
        ("CONTINUE_CONVERSATION", False, f"unmapped intent {intent}"),
    )
    return {"action": action, "execute": execute, "reason": reason, "intent": intent}
