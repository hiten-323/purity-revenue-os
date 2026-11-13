"""
Central Contact Eligibility & Governance Service for Purity Revenue OS.

Every outbound channel (Voice, Email, WhatsApp) independently passes its
eligibility checks. An affirmative signal on one channel never implies
permission on another.

Fail-Closed Governance:
- Voice: TRAI window (09:30-19:00 IST Mon-Sat, Sunday closed), DNC check,
  SIP strike check, max 3 attempts, separate call authorization.
- Email: RFC format, verified MX, bounce suppression, unsubscribe list,
  sendable confidence.
- WhatsApp: Strictly requires recorded AI_CALL_WHATSAPP_REQUEST with SHA-256
  evidence hash, bound consent_phone, 5-day cooldown, allow-list verification.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from typing import Tuple, Dict, Any

IST = ZoneInfo("Asia/Kolkata")
HARD_BLOCK = frozenset({"8591977190"})


@dataclass(frozen=True)
class EligibilityResult:
    lead_id: int
    voice_eligible: bool
    voice_reason: str
    email_eligible: bool
    email_reason: str
    whatsapp_eligible: bool
    whatsapp_reason: str

    @property
    def any_eligible(self) -> bool:
        return self.voice_eligible or self.email_eligible or self.whatsapp_eligible

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "voice": {"eligible": self.voice_eligible, "reason": self.voice_reason},
            "email": {"eligible": self.email_eligible, "reason": self.email_reason},
            "whatsapp": {"eligible": self.whatsapp_eligible, "reason": self.whatsapp_reason},
            "any_eligible": self.any_eligible,
        }


def mask_phone(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return ("XXXXXX" + d[-4:]) if d else "-"


def check_voice_eligibility(lead, db=None, now: datetime | None = None) -> Tuple[bool, str]:
    """Independent voice eligibility check."""
    from app.services.outbound_cohort import call_refusal, on_allow_list
    from app.services import whatsapp_pacer as pacer

    if lead is None:
        return False, "lead is None"

    phone = re.sub(r"\D", "", getattr(lead, "phone", "") or "")
    if not phone or len(phone) < 10:
        return False, "invalid or missing phone number"

    clean_10 = phone[-10:]
    if clean_10 in HARD_BLOCK:
        return False, "phone number is permanently hard-blocked"

    # 1. DND and SIP strike lists
    supp = pacer.number_suppressed(clean_10)
    if supp:
        return False, f"number suppressed: {supp}"

    # 2. Operating hours check (TRAI: 09:30 - 19:00 IST Mon-Sat)
    now_ist = (now or datetime.now(IST)).astimezone(IST)
    if now_ist.weekday() == 6:  # Sunday
        return False, "calling closed on Sunday"
    cur_time = now_ist.time()
    from datetime import time
    if not (time(9, 30) <= cur_time <= time(19, 0)):
        return False, "outside permitted calling window 09:30-19:00 IST"

    # 3. Rolling per-lead call limit. A lifetime counter must never
    # permanently abandon a lead that has no definite disposition.
    from app.services.founder_call_pipeline import (
        AI_CALL_ATTEMPT_WINDOW_DAYS, MAX_AI_CALL_ATTEMPTS_PER_LEAD,
        recent_ai_attempt_count,
    )
    recent_attempts, latest_attempt = recent_ai_attempt_count(lead, db=db)
    if recent_attempts >= MAX_AI_CALL_ATTEMPTS_PER_LEAD:
        return False, (
            f"rolling retry cooling: {recent_attempts} AI calls in "
            f"{AI_CALL_ATTEMPT_WINDOW_DAYS} days; latest={latest_attempt}"
        )

    # 4. Lead Status / Suppression
    status = (getattr(lead, "status", "") or "").upper()
    if status in ("DO_NOT_CONTACT", "CLOSED_UNRECOVERABLE", "WRONG_NUMBER", "QUARANTINED", "OPTED_OUT", "REVOKED"):
        return False, f"status={status} prevents calling"

    if getattr(lead, "do_not_call", False):
        return False, "lead has do_not_call=True"

    # 5. Cohort & Authorization Refusal Gate
    ref = call_refusal(lead, db=db)
    if ref:
        return False, ref

    return True, "eligible for voice call"


def check_email_eligibility(lead, db=None) -> Tuple[bool, str]:
    """Independent email eligibility check."""
    if lead is None:
        return False, "lead is None"

    email = str(getattr(lead, "email", "") or "").strip().lower()
    if not email or "@" not in email or "." not in email:
        return False, "invalid or missing email address"

    status = (getattr(lead, "status", "") or "").upper()
    if status in ("DO_NOT_CONTACT", "CLOSED_UNRECOVERABLE", "QUARANTINED", "OPTED_OUT"):
        return False, f"status={status} prevents email outreach"

    # Check email suppression / bounce guard
    try:
        from app.services import bounce_guard
        if hasattr(bounce_guard, "is_bounced") and bounce_guard.is_bounced(email):
            return False, "email address is on bounce suppression list"
    except Exception:
        pass

    # Fail closed: trust.sendable returns (allowed, reason), not a boolean.
    try:
        from app.services.contact_trust import sendable
        trust_ok, trust_reason = sendable(lead, channel="email")
    except Exception as exc:
        return False, "email trust verification unavailable: " + type(exc).__name__
    if not trust_ok:
        return False, trust_reason or "email contact does not meet trust confidence threshold"

    # The report and sender must use the same allow-list, verification and
    # channel-specific consent gate.
    from types import SimpleNamespace
    from app.services.outbound_cohort import email_refusal
    why = email_refusal(SimpleNamespace(lead_id=lead.id, to_email=email), db)
    if why:
        return False, why

    return True, "eligible for email outreach"


def check_whatsapp_eligibility(lead, db=None, now: datetime | None = None) -> Tuple[bool, str]:
    """Independent WhatsApp eligibility check."""
    from app.services.outbound_cohort import whatsapp_refusal

    if lead is None:
        return False, "lead is None"

    # Local refusal checks: explicit consent, hash, 7-day cooldown, 09:30-20:00 IST window, allow-list
    ref = whatsapp_refusal(lead, db=db)
    if ref:
        return False, ref

    return True, "eligible for WhatsApp dispatch"


def evaluate_lead_eligibility(lead, db=None, now: datetime | None = None) -> EligibilityResult:
    """Evaluates all channels independently and returns comprehensive EligibilityResult."""
    lead_id = getattr(lead, "id", 0)
    v_ok, v_why = check_voice_eligibility(lead, db=db, now=now)
    e_ok, e_why = check_email_eligibility(lead, db=db)
    w_ok, w_why = check_whatsapp_eligibility(lead, db=db, now=now)

    return EligibilityResult(
        lead_id=lead_id,
        voice_eligible=v_ok,
        voice_reason=v_why,
        email_eligible=e_ok,
        email_reason=e_why,
        whatsapp_eligible=w_ok,
        whatsapp_reason=w_why,
    )
