"""Allow-list and the check that runs immediately before a provider call.

The reviewed leads are the only ids the environment is allowed to name.
An environment value can shrink that set. It cannot add a lead.
WhatsApp opt-in is not permission to place another automatic call.
"""
from __future__ import annotations

import json
import os

REVIEWED_ALLOW_LIST = frozenset({550, 1795, 2255, 2259, 2289})
WHATSAPP_OPT_IN_SOURCES = frozenset({
    "AI_CALL_WHATSAPP_REQUEST",
    "WHATSAPP_INBOUND",
    "EMAIL_REPLY",
    "EMAIL_REPLY_WHATSAPP_REQUEST",
})
CALL_AUTHORIZATION_EVENT = "CALL_AUTHORIZATION_RECORDED"
FAILED_OR_BLOCKED = frozenset({
    "FAILED",
    "BLOCKED_NO_PROVIDER_EVENT",
    "BLOCKED_PROVIDER_CONSENT",
    "BLOCKED_DNC",
})


def allow_list_ids() -> frozenset[int]:
    raw = os.getenv("OUTREACH_ALLOW_LIST", "")
    if not str(raw).strip():
        return REVIEWED_ALLOW_LIST
    requested = set()
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit():
            requested.add(int(part))
    return frozenset(requested & set(REVIEWED_ALLOW_LIST))


def on_allow_list(lead) -> bool:
    return getattr(lead, "id", None) in allow_list_ids()


def _queryable(db) -> bool:
    return db is not None and hasattr(db, "query")


def _payload(row) -> dict:
    payload = getattr(row, "payload", None)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def failed_or_blocked_whatsapp(lead, db) -> bool:
    """True when this lead must not be retried, or the ledger cannot be read."""
    if not _queryable(db) or not getattr(lead, "id", None):
        return True
    from app.models.models import OutboundWhatsApp
    row = db.query(OutboundWhatsApp.id).filter(
        OutboundWhatsApp.lead_id == lead.id,
        OutboundWhatsApp.status.in_(tuple(FAILED_OR_BLOCKED)),
    ).first()
    return row is not None


def _separate_call_authorization(lead, db) -> bool:
    if not _queryable(db) or not getattr(lead, "id", None):
        return False
    from app.models.models import WorkflowEvent
    from app.services.gate1_holds import _hash_ok
    destination = _digits(getattr(lead, "phone", None))
    rows = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type == CALL_AUTHORIZATION_EVENT,
    ).all()
    for row in rows:
        payload = _payload(row)
        source = str(payload.get("source") or "").strip()
        if not source or source in WHATSAPP_OPT_IN_SOURCES:
            continue
        if not _hash_ok(payload.get("evidence_sha256")):
            continue
        recorded = _digits(payload.get("consent_phone") or payload.get("phone"))
        if destination and recorded == destination:
            return True
    return False


def call_refusal(lead, db=None) -> str | None:
    """Why this lead must not be dialled. None means the dial may proceed."""
    if not on_allow_list(lead):
        return "lead is outside the calling allow-list"
    from app.services.gate1_holds import nudge_blocked
    held = nudge_blocked(lead, db)
    if held:
        return held
    status = (getattr(lead, "status", "") or "").upper()
    contact = (getattr(lead, "contact_status", "") or "").upper()
    if status in {"DO_NOT_CONTACT", "CLOSED_UNRECOVERABLE", "WRONG_NUMBER", "QUARANTINED"}:
        return "status=" + status
    if contact in {"DO_NOT_CONTACT", "OPTED_OUT", "QUARANTINED"}:
        return "contact_status=" + contact
    if not _separate_call_authorization(lead, db):
        source = (getattr(lead, "consent_source", "") or "").strip() or "empty"
        if source in WHATSAPP_OPT_IN_SOURCES:
            return ("consent_source " + source
                    + " records a WhatsApp request, not a call authorization")
        return "no separate call-authorizing consent record"
    return None


def whatsapp_refusal(lead, db=None) -> str | None:
    """Local WhatsApp gate. Does not subscribe and does not update a flow."""
    if lead is None or not on_allow_list(lead):
        return "lead is outside the WhatsApp allow-list"
    from app.services.gate1_holds import (
        evidence_gaps, later_unbound_consent, lead_has_evidence_hash,
        uncleared_blocked_no_provider_event, uncleared_blocked_provider_consent,
    )
    gaps = evidence_gaps(
        lead,
        evidence_hash_present=lead_has_evidence_hash(lead, db),
        provider_allows=True,
        later_unbound=later_unbound_consent(lead, db),
        blocked_provider_consent=uncleared_blocked_provider_consent(lead, db),
        blocked_no_provider_event=uncleared_blocked_no_provider_event(lead, db),
    )
    if gaps:
        return "; ".join(gaps)
    if not _queryable(db):
        return "WhatsApp eligibility cannot be checked"
    if failed_or_blocked_whatsapp(lead, db):
        return "a failed or blocked WhatsApp row must not be retried"
    from app.services.whatsapp_pacer import eligible, in_hours, slot_free
    ok, why = eligible(db, lead)
    if not ok:
        return why
    if not in_hours():
        return "outside 09:30-19:00 IST Mon-Sat"
    ok, why = slot_free(db)
    if not ok:
        return why
    return None


def before_aisensy(lead, db=None) -> str | None:
    if lead is None:
        return "AiSensy refused before the provider call: no allow-list lead"
    return whatsapp_refusal(lead, db)


def email_refusal(email, db=None) -> str | None:
    """Refuse a prospect email that is not an eligible allow-list lead."""
    lead_id = getattr(email, "lead_id", None)
    if lead_id not in allow_list_ids():
        return "lead is outside the email allow-list"
    if not _queryable(db):
        return "email eligibility cannot be checked"
    from app.models.models import B2BLead
    from app.services.gate1_holds import nudge_blocked
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if lead is None:
        return "lead is outside the email allow-list"
    held = nudge_blocked(lead, db)
    if held:
        return held
    consent = (getattr(lead, "consent_status", "") or "").upper()
    if consent != "EXPLICIT":
        return "consent_status=" + (consent or "UNKNOWN")
    return None


def warm_refusal(lead, db=None) -> str | None:
    if not on_allow_list(lead):
        return "lead is outside the allow-list"
    from app.services.gate1_holds import nudge_blocked
    return nudge_blocked(lead, db)
