"""No-outbound production voice configuration check.

Loads backend/.env, explicitly disarms every outbound channel in this process,
and invokes the personal-call script only in dry-run mode. It never calls the
provider to originate a phone call.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    load_dotenv(backend / ".env")
    # This subprocess is a diagnostic only. Do not inherit live arming flags.
    os.environ.update({
        "SMART_OUTREACH_ENABLED": "0",
        "AUTO_OUTREACH_ENABLED": "0",
        "AISENSY_ENABLED": "0",
        "AI_CALLING_ENABLED": "0",
        "AUTO_AI_CALLING_ENABLED": "0",
    })
    sys.path.insert(0, str(backend))
    try:
        from scripts.personal_test_call import main as personal_test_main
    except Exception as exc:
        print(f"FAIL voice-dry-run-import: {type(exc).__name__}")
        return 1

    output = io.StringIO()
    prior_argv = sys.argv
    try:
        sys.argv = ["personal_test_call.py", "--dry-run"]
        with contextlib.redirect_stdout(output):
            result = personal_test_main()
    except SystemExit as exc:
        result = int(exc.code or 0)
    except Exception as exc:
        print(f"FAIL voice-dry-run-execution: {type(exc).__name__}")
        return 1
    finally:
        sys.argv = prior_argv

    text = output.getvalue()
    provider_ok = bool(re.search(r"provider:\s*True", text))
    sidecar_ok = "'reachable': True" in text and "'http_status': 200" in text
    armed_off = "calling_switched_on: False" in text
    dry_run = "DRY RUN" in text
    if result == 0 and provider_ok and sidecar_ok and armed_off and dry_run:
        print("PASS voice-dry-run: provider configuration and sidecar health verified; outbound flags forced off; no call placed")
        return 0
    reasons = []
    if result != 0:
        reasons.append("dry-run command returned nonzero")
    if not provider_ok:
        reasons.append("provider configuration not verified")
    if not sidecar_ok:
        reasons.append("sidecar health not verified")
    if not armed_off:
        reasons.append("calling-off guard not verified")
    if not dry_run:
        reasons.append("dry-run mode not confirmed")
    print("FAIL voice-dry-run: " + "; ".join(reasons))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
