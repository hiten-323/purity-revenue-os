"""Gate 1 holds. These decisions use flags already stored on the lead.

They do not create a company, phone, email, consent, or delivery status.
"""
from __future__ import annotations

CLOSURE_ACTOR = "GATE1_REMEDIATION"
CLOSURE_REASON = "identity not recovered; masked suffix is not identity."
CLOSED_STATUS = "CLOSED_UNRECOVERABLE"
WA_BLOCKED_STATUS = "BLOCKED_NO_PROVIDER_EVENT"
WA_BLOCK_REASON = "no provider event in the 24-hour window."
REMINDER_REJECTED_STATUS = "REJECTED_NO_CONSENT"
PERMANENTLY_UNMATCHED = "PERMANENTLY_UNMATCHED"
UNMATCHED_REASONS = frozenset({
    "no_local_send_in_24h_window",
    "no_unique_local_lead",
    "no_unique_local_lead_for_destination",
})
RECORDED_CONSENT = frozenset({"EXPLICIT", "OPTED_IN"})
REVOKED_CONSENT = frozenset({"REVOKED", "OPT_OUT", "REFUSED", "UNSUBSCRIBED"})


def _consent(lead) -> str:
    return (getattr(lead, "consent_status", None) or "UNKNOWN").upper()


def ineligibility(lead) -> str | None:
    """Why this lead is already ineligible, or None when no stored flag says so."""
    if bool(getattr(lead, "do_not_call", False)):
        return "do_not_call"
    consent = _consent(lead)
    if consent in REVOKED_CONSENT:
        return f"consent_status={consent}"
    status = (getattr(lead, "status", "") or "").upper()
    if status == "WRONG_NUMBER":
        return "status=WRONG_NUMBER"
    if status == CLOSED_STATUS:
        return f"status={CLOSED_STATUS}"
    if consent not in RECORDED_CONSENT:
        return f"consent_status={consent}"
    return None


def is_send_ready(lead) -> tuple[bool, str]:
    why = ineligibility(lead)
    if why:
        return False, why
    contact = (getattr(lead, "contact_status", "") or "CONTACTABLE").upper()
    if contact not in {"CONTACTABLE", "FOLLOW_UP"}:
        return False, f"contact_status={contact}"
    return True, "eligible"


def contact_status_correction(lead) -> tuple[str, str] | None:
    """Return (DO_NOT_CONTACT, reason) only for a CONTACTABLE lead that is already ineligible."""
    if (getattr(lead, "contact_status", "") or "").upper() != "CONTACTABLE":
        return None
    parts = []
    if bool(getattr(lead, "do_not_call", False)):
        parts.append("do_not_call")
    consent = _consent(lead)
    if consent in REVOKED_CONSENT:
        parts.append(f"consent_status={consent}")
    status = (getattr(lead, "status", "") or "").upper()
    if status == "WRONG_NUMBER":
        parts.append("status=WRONG_NUMBER")
    if consent not in RECORDED_CONSENT and consent not in REVOKED_CONSENT:
        parts.append(f"consent_status={consent}")
    if not parts:
        return None
    return "DO_NOT_CONTACT", "not send-ready: " + ", ".join(parts)


def close_unrecoverable_fields(current: dict, now_iso: str, actor: str = CLOSURE_ACTOR) -> dict:
    """Terminal closure for a lead whose identity was not recovered.

    Refuses when a phone or email is already stored. Does not fill either,
    and does not clear DNC, consent, or the existing contact status.
    """
    if any(str(current.get(key) or "").strip() for key in ("phone", "email", "whatsapp_number", "consent_phone")):
        raise RuntimeError("refusing to close a lead that already has contact details")
    if int(current.get("do_not_call") or 0) != 1:
        raise RuntimeError("refusing to close a lead whose DNC flag is not set")
    if (current.get("consent_status") or "UNKNOWN").upper() != "UNKNOWN":
        raise RuntimeError("refusing to close a lead whose consent is not UNKNOWN")
    return {
        "status": CLOSED_STATUS,
        "do_not_call": 1,
        "dnc_reason": current.get("dnc_reason"),
        "consent_status": "UNKNOWN",
        "contact_status": current.get("contact_status"),
        "company": current.get("company"),
        "phone": None,
        "email": None,
        "whatsapp_number": None,
        "event": {
            "event_type": "LEAD_CLOSED_UNRECOVERABLE",
            "actor": actor,
            "occurred_at": now_iso,
            "before_status": current.get("status"),
            "after_status": CLOSED_STATUS,
            "channel": "system",
            "payload": {
                "reason": CLOSURE_REASON,
                "identity_recovered": False,
                "contact_added": False,
            },
        },
    }


def block_queue_without_provider_event(status: str, response: str | None) -> dict:
    """Move one queued row to a terminal block. Does not call it sent or delivered."""
    if status != "QUEUED_TO_KLAVIYO":
        raise RuntimeError("only QUEUED_TO_KLAVIYO can take this terminal block")
    import json
    body = {
        "provider": "klaviyo",
        "reconciliation": "blocked",
        "terminal": True,
        "requeue": False,
        "reason": WA_BLOCK_REASON,
        "prior_response": response,
    }
    return {
        "status": WA_BLOCKED_STATUS,
        "whatsapp_response": json.dumps(body, ensure_ascii=False),
        "delivered_at_unchanged": True,
    }


def unmatched_reason_allowed(reason: str) -> bool:
    return reason in UNMATCHED_REASONS


def sweep_protected(lead) -> bool:
    """A startup trust sweep must not rewrite these holds."""
    if bool(getattr(lead, "do_not_call", False)):
        return True
    status = (getattr(lead, "status", "") or "").upper()
    if status in {CLOSED_STATUS, "WRONG_NUMBER"}:
        return True
    if (getattr(lead, "contact_status", "") or "").upper() == "DO_NOT_CONTACT":
        return True
    consent = _consent(lead)
    if consent in REVOKED_CONSENT or consent not in RECORDED_CONSENT:
        return True
    return False


def consent_phone_matches_destination(lead) -> bool:
    """Same subscriber number, ignoring +, spaces, and hyphens."""
    from app.services.identity import digits_only
    bound = digits_only(getattr(lead, "consent_phone", None))
    current = digits_only(getattr(lead, "whatsapp_number", None) or getattr(lead, "phone", None))
    return bool(bound) and bool(current) and bound == current


def _hash_ok(value) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", str(value or "")))


def evidence_hash_present(db, lead_id) -> bool:
    """True only when a recorded consent event carries a sha256 evidence hash."""
    if db is None or not lead_id:
        return False
    import json
    from app.models.models import WorkflowEvent
    rows = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead_id,
        WorkflowEvent.event_type == "WHATSAPP_CONSENT_RECORDED",
    ).all()
    for row in rows:
        payload = row.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if isinstance(payload, dict) and _hash_ok(payload.get("evidence_sha256")):
            return True
    return False


def lead_has_evidence_hash(lead, db=None) -> bool:
    if _hash_ok(getattr(lead, "evidence_sha256", None)):
        return True
    return evidence_hash_present(db, getattr(lead, "id", None))


def provider_subscription_allows(phone: str) -> tuple[bool, str]:
    """Read-only Klaviyo lookup. Never subscribes and never returns the number."""
    from app.services.whatsapp_klaviyo import e164_in, wa_status
    e164 = e164_in(phone or "")
    if not e164:
        return False, "destination is not e164"
    try:
        _profile_id, ok, state = wa_status(e164)
    except Exception as exc:
        return False, "lookup failed: " + type(exc).__name__
    if ok:
        return True, "SUBSCRIBED"
    return False, state or "not subscribed"


def evidence_gaps(lead, *, evidence_hash_present: bool, provider_allows: bool,
                  provider_detail: str = "") -> list[str]:
    """Missing facts that keep a lead from being dispatched. No contact is invented."""
    gaps = []
    consent = _consent(lead)
    if consent != "EXPLICIT":
        gaps.append(f"consent_status={consent}")
    if consent in REVOKED_CONSENT:
        gaps.append(f"revocation consent_status={consent}")
    status = (getattr(lead, "status", "") or "").upper()
    if status in {"OPT_OUT", "UNSUBSCRIBED"} or consent in {"OPT_OUT", "UNSUBSCRIBED"}:
        gaps.append("opt-out")
    if not consent_phone_matches_destination(lead):
        gaps.append("consent phone does not exactly match the current destination")
    if not evidence_hash_present:
        gaps.append("evidence hash missing")
    if not provider_allows:
        gaps.append("provider subscription does not allow the send: " + (provider_detail or "not confirmed"))
    if bool(getattr(lead, "do_not_call", False)):
        gaps.append("do_not_call")
    if status == "WRONG_NUMBER":
        gaps.append("status=WRONG_NUMBER")
    return gaps


def evidence_refusal(lead, db=None) -> str | None:
    """Why dispatch must stop, or None when every stored check passes.

    The provider is asked only after the local checks pass, and only by the
    read-only subscription lookup.
    """
    hash_ok = lead_has_evidence_hash(lead, db)
    local = evidence_gaps(lead, evidence_hash_present=hash_ok, provider_allows=True)
    if local:
        return "; ".join(local)
    phone = getattr(lead, "consent_phone", None) or getattr(lead, "whatsapp_number", None) or getattr(lead, "phone", None)
    allows, detail = provider_subscription_allows(phone or "")
    if not allows:
        return "provider subscription does not allow the send: " + detail
    return None


def reminder_may_dispatch(lead, channel: str, reminder_status: str = "SCHEDULED") -> tuple[bool, str]:
    """Reject a scheduled reminder. Outbound flags are not an input."""
    if (reminder_status or "").upper() != "SCHEDULED":
        return False, "reminder is not scheduled"
    channel = (channel or "").lower()
    consent = _consent(lead)
    if channel == "whatsapp" and consent not in RECORDED_CONSENT:
        return False, f"consent_status={consent}"
    why = ineligibility(lead)
    if why:
        return False, why
    if (getattr(lead, "status", "") or "").upper() in {"QUARANTINED", CLOSED_STATUS, "WRONG_NUMBER"}:
        return False, "status=" + (getattr(lead, "status", "") or "").upper()
    return True, "allowed"
