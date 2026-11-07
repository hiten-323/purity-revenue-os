r"""
Which voice agent places the call. One selection point, not two.

There is one voice adapter:

    nuraveda_provider  Nuraveda / Mesh Pilot, LiveKit + SIP or Twilio trunk

There were two. voice_provider (Bolna over Exotel) was removed once Nuraveda
became the voice agent; keeping a second adapter that nobody configures is how
a config gate ends up validating one provider while the code dials another.

This module stays even with one provider, because it owns two things that
should not move into an adapter: CallResult, and the rule that an unknown
VOICE_PROVIDER is refused rather than guessed.

Before this module the choice was made by whoever happened to be writing the
call site, which is how a system ends up dialling through one provider while
its configuration gate validates the other — precisely what
calling_agent.trigger_vapi_call did: it checked BOLNA credentials and then
POSTed to VAPI.

So the provider is named once, in config, and every dial goes through here.

    VOICE_PROVIDER=nuraveda    (default, and currently the only one)

What this module does NOT do
----------------------------
It does not decide who may be called. Neither do the adapters. Permission is
decided upstream, once, for every AI call:

    founder_call_pipeline.may_place_ai_call()

There is no separate consented-call path: email/WhatsApp consent is not
consent to be called. A router that also re-checked consent would be a second
opinion on one question, and the two would eventually disagree. Its whole job is to turn
"place this call" into the right adapter's idea of that sentence.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class CallResult:
    """What a voice adapter reports back. One shape, so callers do not branch.

    This lived in voice_provider (the Bolna adapter). When Bolna was removed
    the contract had to outlive it — a shared result type owned by one of the
    implementations is a dependency waiting to break, which is exactly what
    happened here.

    `placed` means the provider ACCEPTED the call. It never means anyone
    answered.
    """
    placed: bool
    provider_call_id: str = ""
    error: str = ""
    raw: dict | None = None


NURAVEDA = "nuraveda"
PROVIDERS = (NURAVEDA,)


def active() -> str:
    """The configured provider. An unknown name is refused, not guessed."""
    return (os.getenv("VOICE_PROVIDER", "") or NURAVEDA).strip().lower()


def _adapter(name: str):
    from app.services import nuraveda_provider as mod
    return mod


def config_status() -> tuple[bool, str]:
    """Could a call actually be placed right now, and if not, what is missing?

    Reported for the ACTIVE provider only. Listing what the other one needs
    would be noise at best and a false green at worst.
    """
    name = active()
    if name not in PROVIDERS:
        return False, (f"VOICE_PROVIDER={name!r} is not a known provider; "
                       f"expected one of {', '.join(PROVIDERS)}")
    ok, detail = _adapter(name).config_status()
    return ok, f"{name}: {detail}"


def idempotency_key(lead) -> str:
    """One call per lead per day, stable across retries.

    Both services dedupe on a caller-supplied key, so an unstable one turns a
    retry into a second phone ringing in the same shop.
    """
    return f"purity-lead-{getattr(lead, 'id', 'x')}-{date.today():%Y%m%d}"


def _flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def calling_switched_on() -> bool:
    """Process-wide arming switch. Default OFF.

    Distinct from AI_CALLING_KILL_SWITCH: that is an emergency stop.
    This is the production arm. Both must allow a dial.
    """
    return _flag("AI_CALLING_ENABLED")


def kill_switch_engaged() -> bool:
    """Global stop for every outbound AI voice call, cold or consented.

    Not a consent opinion — a consent opinion answers "may THIS lead be
    called"; this answers "is calling switched on for anyone right now".
    Checked here, in the one function every dial (cold and consented alike)
    already passes through, rather than in may_place_ai_call() — that gate
    only covers the cold path, and a kill switch that missed the consented
    path would not be one.
    """
    return _flag("AI_CALLING_KILL_SWITCH", default="")


def place_call(lead, *, context: dict[str, Any] | None = None,
               dry_run: bool = False, scheduled_at=None, db=None) -> CallResult:
    """Dial through whichever provider is configured.

    The lead is used for context and for the idempotency key. It is NOT used
    to decide permission — see the module docstring.

    scheduled_at is an explicit override of the provider's normal dispatch
    delay (nuraveda defaults to CALL_DELAY_MS, ~10 minutes, DND-rolled). It
    exists for an operator-requested immediate test call, not for routine
    dispatch — leaving it None preserves every existing call site's timing
    exactly as before this parameter was added.

    Business hours are enforced HERE as well as in calling_agent.check_eligibility:
    this is the single dial path, so a caller that skips the service (or a
    scheduler bug) still cannot ring during a closed window. The gate reads
    AI_CALL_BUSINESS_HOURS (default "1"), i.e. it is process-wide and on in
    production; dry-run and tests turn it off explicitly.
    """
    if kill_switch_engaged():
        return CallResult(placed=False, error="AI_CALLING_KILL_SWITCH is engaged — no outbound AI calls")
    if not calling_switched_on():
        return CallResult(placed=False, error="AI_CALLING_ENABLED is not set to 1 — no outbound AI calls")

    from app.services import founder_call_pipeline as _pipeline
    hours_ok, hours_why = _pipeline.call_hours_open(lead=lead)
    if not hours_ok:
        return CallResult(placed=False, error=f"business_hours_closed: {hours_why}")

    name = active()
    ok, detail = config_status()
    if not ok:
        return CallResult(placed=False, error=detail)

    # 4 Nov 2026 (Hiten "stop,fix all,resume"): NEVER_DIAL hard block + founder
    # do-not-call businesses, at the final chokepoint (not only in prepare_dial).
    from app.services.call_intelligence.standing_rules import call_blocked as _standing_block
    _sb = _standing_block(lead)
    if _sb:
        return CallResult(placed=False, error=f"DNC_BLOCKED: {_sb}")
    # 4 Nov 2026: duplicate-phone rows are never dialled (only the canonical row).
    _dup = _pipeline.duplicate_phone_reason(lead)
    if _dup:
        return CallResult(placed=False, error=f"DNC_BLOCKED: {_dup}")
    _cs = (getattr(lead, "contact_status", "") or "").upper()
    if _cs in ("OPTED_OUT", "DO_NOT_CONTACT", "SUPPRESSED"):
        return CallResult(placed=False, error=f"DNC_BLOCKED: contact_status={_cs}")

    # Check do_not_call / DNC flags
    if getattr(lead, "do_not_call", 0) or getattr(lead, "do_not_call", False):
        dnc_why = getattr(lead, "dnc_reason", None) or "do_not_call flag set"
        return CallResult(placed=False, error=f"DNC_BLOCKED: {dnc_why}")

    phone = (getattr(lead, "phone", "") or "").strip()
    if not phone:
        return CallResult(placed=False, error="no phone on record")
    # 3 Nov 2026 fix 2 (Hiten 11:49 IST): the final dial chokepoint refuses a
    # blocked phone_trust too (incl. QUARANTINED shared directory numbers), so
    # no caller that skips may_place_ai_call can ring one.
    _trust = (getattr(lead, "phone_trust", None) or "").upper()
    if _trust in _pipeline.PHONE_TRUST_BLOCKED:
        return CallResult(placed=False, error=f"phone_trust={_trust} - number blocked, not dialled")

    # 3 Nov 2026 fix 3: unproven scraped +91 6-series number held for verification.
    try:
        from app.services.call_intelligence.six_series_hold import hold_reason as _hold
        _h = _hold(None, lead)
    except Exception:  # noqa: BLE001
        _h = None
    if _h:
        return CallResult(placed=False, error=_h)

    if not dry_run:
        from app.services.gate1_holds import evidence_refusal
        held = evidence_refusal(lead)
        if held:
            return CallResult(placed=False, error=held)

    ctx = {
        "lead_id": getattr(lead, "id", "") or "",
        "company": getattr(lead, "company", "") or "",
        "contact": getattr(lead, "contact_name", "") or "",
        "city": getattr(lead, "city", "") or "",
        "segment": getattr(lead, "segment", "") or "",
        **(context or {}),
        # Set after caller context so this policy marker cannot be overridden.
        # The sidecar needs it to preserve the exact-callback exception.
        # 3 Nov 2026 dial-guard fix: only a callback due NOW lifts the sidecar's window.
        "explicit_callback": bool(_pipeline.explicit_callback_due(lead)[0]),
    }
    # Every dial must carry the disclosed opening. Call sites that omit it
    # (e.g. an early personal_test) used to hit a long Hindi profile fallback
    # that mismatched male TTS and got cut off mid-line on PSTN.
    if not (ctx.get("opening") or "").strip():
        from app.services import founder_call_pipeline as pipeline
        ctx["opening"] = pipeline.opening_for(lead)

    from app.services.outbound_cohort import call_refusal
    refused = call_refusal(lead, db)
    if refused:
        return CallResult(placed=False, error=refused)

    mod = _adapter(name)
    return mod.place_call(phone, context=ctx, dry_run=dry_run,
                          idempotency_key=idempotency_key(lead),
                          scheduled_at=scheduled_at)
