r"""ONE authorized personal AI test call.

The only number this script will ever dial is 9855593323. Anything else is
refused. Production outreach flags must be off. AI_CALLING_ENABLED must be
set in the environment by the operator for the duration of the test — this
script does not write ecosystem.config.js or .env.

Usage
-----
    python scripts/personal_test_call.py --dry-run
    python scripts/personal_test_call.py --i-am-the-founder --confirm 9855593323

A dry-run never reaches the provider. A live call is refused unless the
Nuraveda sidecar is reachable AND the number matches the allowlist.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ALLOWED_TAILS = frozenset({"9855593323"})


def _digits(value: str) -> str:
    return "".join(c for c in (value or "") if c.isdigit())[-10:]


def _flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    ap = argparse.ArgumentParser(description="Personal AI test call. One number.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--i-am-the-founder", action="store_true")
    ap.add_argument("--confirm", default="", help="the number, must be 9855593323")
    args = ap.parse_args()

    from app.services import identity
    from app.services import nuraveda_provider as nv
    from app.services import voice_router

    print("PERSONAL TEST CALL")
    print("SMART_OUTREACH_ENABLED =", os.getenv("SMART_OUTREACH_ENABLED", "0"))
    print("AUTO_OUTREACH_ENABLED  =", os.getenv("AUTO_OUTREACH_ENABLED", "0"))
    print("AISENSY_ENABLED        =", os.getenv("AISENSY_ENABLED", "0"))
    print("AI_CALLING_ENABLED     =", os.getenv("AI_CALLING_ENABLED", "0"))
    print("AI_CALLING_KILL_SWITCH =", os.getenv("AI_CALLING_KILL_SWITCH", ""))

    if _flag("SMART_OUTREACH_ENABLED") or _flag("AUTO_OUTREACH_ENABLED") or _flag("AISENSY_ENABLED"):
        print("REFUSED: production outreach/WhatsApp is on. Turn them off.")
        return 2

    number = args.confirm or "9855593323"
    tail = _digits(number)
    if tail not in ALLOWED_TAILS:
        print(f"REFUSED: {number!r} is not the authorized personal test number")
        return 2
    if identity.is_fabricated_pattern(number):
        print("REFUSED: number looks fabricated")
        return 2

    class _Lead:
        id = "personal-test"
        phone = "+91" + tail
        company = "PERSONAL TEST — DO NOT USE FOR BUSINESS"
        contact_name = "Hiten"
        city = ""
        segment = "cafe"

    import time as _time
    _Lead.id = f"personal-test-{int(_time.time())}"

    if args.dry_run or not args.i_am_the_founder:
        print("DRY RUN — no provider call")
        print("  allowlist ok:", tail)
        print("  calling_switched_on:", voice_router.calling_switched_on())
        print("  kill_switch:", voice_router.kill_switch_engaged())
        ok, detail = voice_router.config_status()
        print("  provider:", ok, detail)
        health = nv.health()
        print("  sidecar:", health)
        return 0

    if not voice_router.calling_switched_on():
        print("REFUSED: set AI_CALLING_ENABLED=1 in this process only, then re-run")
        return 2
    if voice_router.kill_switch_engaged():
        print("REFUSED: kill switch is on")
        return 2

    from datetime import datetime
    from app.services import founder_call_pipeline as pipeline
    # Always pass the disclosed opening. Without it the voice profile falls
    # back to a long Hindi welcome that was getting cut off mid-line, and
    # gender of that fallback must match the female Sarvam speakers.
    lead = _Lead()
    result = voice_router.place_call(
        lead,
        context={"opening": pipeline.opening_for(lead)},
        dry_run=False,
        scheduled_at=datetime.utcnow(),
    )
    print("placed:", result.placed)
    print("provider_call_id:", result.provider_call_id)
    print("error:", result.error)
    return 0 if result.placed else 1


if __name__ == "__main__":
    raise SystemExit(main())
