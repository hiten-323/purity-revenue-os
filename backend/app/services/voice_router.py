r"""
Which voice agent places the call. One selection point, not two.

There are two adapters in this codebase:

    voice_provider     Bolna agent over Exotel telephony
    nuraveda_provider  Nuraveda / Mesh Pilot, LiveKit + SIP or Twilio trunk

Before this module the choice was made by whoever happened to be writing the
call site, which is how a system ends up dialling through one provider while
its configuration gate validates the other — precisely what
calling_agent.trigger_vapi_call did: it checked BOLNA credentials and then
POSTed to VAPI.

So the provider is named once, in config, and every dial goes through here.

    VOICE_PROVIDER=bolna       (default)
    VOICE_PROVIDER=nuraveda

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
from datetime import date
from typing import Any

from app.services.voice_provider import CallResult

BOLNA = "bolna"
NURAVEDA = "nuraveda"
PROVIDERS = (BOLNA, NURAVEDA)


def active() -> str:
    """The configured provider. An unknown name is refused, not guessed."""
    name = (os.getenv("VOICE_PROVIDER", "") or BOLNA).strip().lower()
    return name if name in PROVIDERS else name  # validated in config_status()


def _adapter(name: str):
    if name == NURAVEDA:
        from app.services import nuraveda_provider as mod
        return mod
    from app.services import voice_provider as mod
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


def place_call(lead, *, context: dict[str, Any] | None = None,
               dry_run: bool = False) -> CallResult:
    """Dial through whichever provider is configured.

    The lead is used for context and for the idempotency key. It is NOT used
    to decide permission — see the module docstring.
    """
    name = active()
    ok, detail = config_status()
    if not ok:
        return CallResult(placed=False, error=detail)

    phone = (getattr(lead, "phone", "") or "").strip()
    if not phone:
        return CallResult(placed=False, error="no phone on record")

    ctx = {
        "company": getattr(lead, "company", "") or "",
        "contact": getattr(lead, "contact_name", "") or "",
        "city": getattr(lead, "city", "") or "",
        "segment": getattr(lead, "segment", "") or "",
        **(context or {}),
    }

    mod = _adapter(name)
    if name == NURAVEDA:
        return mod.place_call(phone, context=ctx, dry_run=dry_run,
                              idempotency_key=idempotency_key(lead))
    return mod.place_call(phone, context=ctx, dry_run=dry_run)
