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
decided upstream, once:

    cold qualification call  -> founder_call_pipeline.may_place_ai_call()
    consented call           -> check_eligibility's consent clause

A router that also re-checked consent would be the third opinion on one
question, and the three would eventually disagree. Its whole job is to turn
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


def kill_switch_engaged() -> bool:
    """Global stop for every outbound AI voice call, cold or consented.

    Not a consent opinion — a consent opinion answers "may THIS lead be
    called"; this answers "is calling switched on for anyone right now".
    Checked here, in the one function every dial (cold and consented alike)
    already passes through, rather than in may_place_ai_call() — that gate
    only covers the cold path, and a kill switch that missed the consented
    path would not be one.
    """
    return (os.getenv("AI_CALLING_KILL_SWITCH", "") or "").strip().lower() in ("1", "true", "yes", "on")


def place_call(lead, *, context: dict[str, Any] | None = None,
               dry_run: bool = False, scheduled_at=None) -> CallResult:
    """Dial through whichever provider is configured.

    The lead is used for context and for the idempotency key. It is NOT used
    to decide permission — see the module docstring.

    scheduled_at is an explicit override of the provider's normal dispatch
    delay (nuraveda defaults to CALL_DELAY_MS, ~10 minutes, DND-rolled). It
    exists for an operator-requested immediate test call, not for routine
    dispatch — leaving it None preserves every existing call site's timing
    exactly as before this parameter was added.
    """
    if kill_switch_engaged():
        return CallResult(placed=False, error="AI_CALLING_KILL_SWITCH is engaged — no outbound AI calls")

    name = active()
    ok, detail = config_status()
    if not ok:
        return CallResult(placed=False, error=detail)

    phone = (getattr(lead, "phone", "") or "").strip()
    if not phone:
        return CallResult(placed=False, error="no phone on record")

    ctx = {
        "lead_id": getattr(lead, "id", "") or "",
        "company": getattr(lead, "company", "") or "",
        "contact": getattr(lead, "contact_name", "") or "",
        "city": getattr(lead, "city", "") or "",
        "segment": getattr(lead, "segment", "") or "",
        **(context or {}),
    }

    mod = _adapter(name)
    return mod.place_call(phone, context=ctx, dry_run=dry_run,
                          idempotency_key=idempotency_key(lead),
                          scheduled_at=scheduled_at)
