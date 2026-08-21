"""
Voice provider adapter — Bolna (agent) over Exotel (telephony).

Chosen over VAPI/Twilio because both are India-native: Exotel holds Indian
carrier relationships, DLT registration and DND handling as first-class
concerns, and Bolna speaks Hindi and Punjabi — which matters when the call
queue is kirana stores in Bathinda and distributors in Ludhiana. An
English-only agent would fail on exactly the highest-fit segment.

DEFAULT OFF. AI_CALLING_ENABLED must be "1" and every credential present
before this can dial. Same pattern as AUTO_OUTREACH_ENABLED: the kill switch
lives in code, not only in config.

This adapter PLACES calls. It does not decide who may be called — consent,
DND, attempt caps and cooldown belong to CallingAgentService, which runs its
gates before this is ever reached.

VERIFY BEFORE ENABLING: the request shape below follows Bolna's documented
/call endpoint, but I have not exercised it against a live account. Treat the
first call as a test against your own number, not a prospect. A wrong payload
fails loudly here rather than silently — no result is ever synthesised.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

BOLNA_BASE = os.getenv("BOLNA_BASE_URL", "https://api.bolna.dev")


@dataclass(frozen=True)
class CallResult:
    placed: bool
    provider_call_id: str = ""
    error: str = ""
    raw: dict | None = None


def enabled() -> bool:
    return (os.getenv("AI_CALLING_ENABLED", "0") or "0").strip() == "1"


def config_status() -> tuple[bool, str]:
    """
    Whether a real call could be placed. Reports every missing piece at once —
    discovering them one failed call at a time wastes attempts against
    MAX_CALL_ATTEMPTS on leads that were never dialled.
    """
    if not enabled():
        return False, "AI_CALLING_ENABLED is not 1 — voice calling is off"
    missing = [k for k in ("BOLNA_API_KEY", "BOLNA_AGENT_ID")
               if not (os.getenv(k) or "").strip()]
    # Exotel is configured inside Bolna as the telephony provider, so its
    # credentials are only required when this app talks to Exotel directly.
    # BOLNA_FROM_NUMBER is the Exotel virtual number the agent dials from.
    if not (os.getenv("BOLNA_FROM_NUMBER") or "").strip():
        missing.append("BOLNA_FROM_NUMBER (your Exotel virtual number)")
    if missing:
        return False, "not configured: " + ", ".join(missing)
    return True, "configured"


def _post(url: str, payload: dict, api_key: str, timeout: int = 30) -> tuple[int, dict]:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body or "{}")
            except json.JSONDecodeError:
                return r.status, {"raw": body[:400]}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400] if e.fp else ""
        return e.code, {"error": detail or e.reason}
    except Exception as e:
        return 0, {"error": f"{e.__class__.__name__}: {e}"}


def place_call(phone: str, *, context: dict[str, Any] | None = None,
               dry_run: bool = False) -> CallResult:
    """
    Place one outbound agent call. Returns what the provider actually said.

    dry_run validates configuration and the payload without dialling, so the
    wiring can be proven before a real number rings.
    """
    ok, why = config_status()
    if not ok:
        return CallResult(False, error=why)

    number = (phone or "").strip()
    if not number:
        return CallResult(False, error="no phone number")
    # E.164 for India. Exotel rejects unnormalised numbers, and a rejected
    # call still costs an attempt upstream if we let it through.
    digits = "".join(ch for ch in number if ch.isdigit())
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    if not (len(digits) == 12 and digits.startswith("91")):
        return CallResult(False, error=f"not a valid Indian number: {number!r}")
    e164 = "+" + digits

    payload = {
        "agent_id": (os.getenv("BOLNA_AGENT_ID") or "").strip(),
        "recipient_phone_number": e164,
        "from_phone_number": (os.getenv("BOLNA_FROM_NUMBER") or "").strip(),
    }
    if context:
        # Bolna substitutes these into the agent prompt. Only real, stored
        # values are passed — never a guess about the business.
        payload["variables"] = {k: str(v) for k, v in context.items()
                                if v not in (None, "")}

    if dry_run:
        return CallResult(False, error="dry_run: not dialled",
                          raw={"would_post": f"{BOLNA_BASE}/call", "payload": payload})

    status, body = _post(f"{BOLNA_BASE}/call", payload,
                         (os.getenv("BOLNA_API_KEY") or "").strip())
    if status in (200, 201, 202):
        cid = str(body.get("call_id") or body.get("id") or "")
        if not cid:
            # Accepted but unidentifiable. Treat as NOT placed: without an id
            # the call cannot be reconciled to an outcome later, and an
            # unverifiable send is the thing the send-proof listener exists to
            # prevent on the email side.
            return CallResult(False, error=f"provider accepted but returned no call id: {body}",
                              raw=body)
        return CallResult(True, provider_call_id=cid, raw=body)
    return CallResult(False, error=f"provider HTTP {status}: {body.get('error') or body}",
                      raw=body)
