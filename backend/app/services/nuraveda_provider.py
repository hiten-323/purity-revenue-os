"""
Adapter for the Nuraveda / Mesh Pilot AI Voice Agent.

    https://github.com/Nuraveda-Labs/ai-voice-agent   (MIT)

What it is
----------
A separate Node service — LiveKit voice loop, Prisma/Postgres queue, SIP or
Twilio trunk for the actual PSTN leg. It is not a Python library, so this file
is a client, not a wrapper: Revenue OS enqueues a call and the Node service
owns the conversation.

Written against the real source, not against a guess:

    POST /calls/dispatch     enqueue an outbound call     (src/server.js)
      auth   X-COD-Tool-Secret: <LIVEKIT_TOOL_SECRET>, fails closed
      body   {profile, phone, lang?, payload?, scheduledAt?, idempotencyKey?}
      200    {ok, id, profile, scheduledAt, orderId, orderName, reused}
    GET  /health             dispatch_mode, queue depth, DND, config flags
    GET  /profiles           which call profiles the service has loaded

Two things the upstream docs get wrong, corrected here:

* .env.example advertises HTTP_PORT=8081; server.js actually reads
  `process.env.PORT || 3104`. 3104 is the default used below.
* The service binds 127.0.0.1 only, so it is reachable from this host and
  nowhere else. That is a feature — treat it as a localhost sidecar.

Consent is decided HERE, before dispatch
----------------------------------------
The service has its own DND window and its own idempotency, and both are good.
Neither knows anything about whether a Purity Beans lead agreed to be called.
CallingAgentService.CALL_ALLOWED_IF is the only list that decides that, and it
is imported rather than restated so a second opinion cannot form. A voice
integration that skipped it would be the fifth write path in this codebase to
route around a gate that already exists.

Placing a call is also the least reversible thing this system can do. A wrong
email is a wrong email; a wrong call is a real phone ringing in a real shop.
So: disabled by default, dry-run available, and refusal is the default answer
to every question this module cannot answer with evidence.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date
from typing import Any

from app.services.voice_provider import CallResult

DEFAULT_URL = "http://127.0.0.1:3104"
DEFAULT_PROFILE = "ai-voice-agent"
DEFAULT_LANG = "hi-IN"
TIMEOUT = 20


def enabled() -> bool:
    """Off unless explicitly switched on. Never defaults to dialling."""
    return (os.getenv("NURAVEDA_ENABLED", "0") or "0").strip().lower() in ("1", "true", "yes", "on")


def base_url() -> str:
    return (os.getenv("NURAVEDA_URL", "") or DEFAULT_URL).strip().rstrip("/")


def _secret() -> str:
    return (os.getenv("NURAVEDA_TOOL_SECRET", "") or "").strip()


def profile() -> str:
    return (os.getenv("NURAVEDA_PROFILE", "") or DEFAULT_PROFILE).strip()


def config_status() -> tuple[bool, str]:
    """What is missing, named precisely, so the answer is actionable."""
    if not enabled():
        return False, "NURAVEDA_ENABLED is not set to 1"
    if not _secret():
        return False, ("NURAVEDA_TOOL_SECRET is not set. It must equal the "
                       "LIVEKIT_TOOL_SECRET the Node service runs with, or "
                       "/calls/dispatch returns 401.")
    return True, f"configured for {base_url()} profile={profile()}"


def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    url = f"{base_url()}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if _secret():
        req.add_header("X-COD-Tool-Secret", _secret())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {"raw": body[:500]}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:500]}
    except Exception as exc:  # noqa: BLE001 - unreachable is a status, not a crash
        return 0, {"error": f"{exc.__class__.__name__}: {exc}"}


def health() -> dict[str, Any]:
    """Is the sidecar up, and is it in live or dry-run mode?

    dispatch_mode is the service's own kill switch and is reported verbatim.
    A caller should not assume 'reachable' means 'will actually dial'.
    """
    status, body = _request("GET", "/health")
    return {
        "reachable": status == 200,
        "http_status": status,
        "dispatch_mode": body.get("dispatch_mode"),
        "live": bool(body.get("live")),
        "livekit_configured": bool(body.get("livekit_agent_configured")),
        "queue": body.get("queue"),
        "in_dnd_now": body.get("in_dnd_now"),
        "error": body.get("error"),
    }


def may_call(lead) -> tuple[bool, str]:
    """Consent, decided by the authority that already owns it.

    CALL_ALLOWED_IF is imported, not restated. If the import fails we refuse:
    a dial path that cannot reach the consent rule has not satisfied it.
    """
    try:
        from app.services.calling_agent import CallingAgentService
    except Exception as exc:  # noqa: BLE001
        return False, f"consent rule unavailable ({exc.__class__.__name__}); refusing to dial"

    if getattr(lead, "do_not_call", False):
        return False, "do_not_call is set on this lead"
    status = (getattr(lead, "consent_status", None) or "UNKNOWN").upper()
    if status not in CallingAgentService.CALL_ALLOWED_IF:
        return False, (f"no consent on record (consent_status={status}); "
                       f"allowed: {', '.join(CallingAgentService.CALL_ALLOWED_IF)}")
    return True, status


def place_call(lead, *, context: dict[str, Any] | None = None,
               lang: str | None = None, dry_run: bool = False) -> CallResult:
    """Enqueue one outbound call for a lead, or explain why not.

    Returns voice_provider.CallResult so the Bolna adapter and this one report
    through one shape rather than two.
    """
    phone = (getattr(lead, "phone", "") or "").strip()
    if not phone:
        return CallResult(placed=False, error="no phone on record")

    allowed, why = may_call(lead)
    if not allowed:
        return CallResult(placed=False, error=f"consent gate: {why}")

    ok, detail = config_status()
    if not ok:
        return CallResult(placed=False, error=detail)

    # One call per lead per day. The service enforces uniqueness on
    # (shop, orderId), so a stable key makes a retry a no-op instead of a
    # second phone call to the same shopkeeper.
    idem = f"purity-lead-{getattr(lead, 'id', 'x')}-{date.today():%Y%m%d}"

    payload = {
        "profile": profile(),
        "phone": phone,
        "lang": (lang or os.getenv("NURAVEDA_LANG", "") or DEFAULT_LANG).strip(),
        "idempotencyKey": idem,
        "orderName": f"purity:{getattr(lead, 'company', '') or 'lead'}"[:80],
        "payload": {
            "customer_name": getattr(lead, "contact_name", "") or "",
            "company": getattr(lead, "company", "") or "",
            "city": getattr(lead, "city", "") or "",
            **(context or {}),
        },
    }

    if dry_run:
        return CallResult(placed=False, error="dry run — not dispatched",
                          raw={"would_post": f"{base_url()}/calls/dispatch",
                               "payload": payload})

    status, body = _request("POST", "/calls/dispatch", payload)

    # A 2xx without an id is not a queued call. The Bolna adapter learned this
    # the same way: a provider that answers politely has not necessarily done
    # anything, and treating "ok" as "dialled" invents outcomes.
    if 200 <= status < 300 and body.get("id"):
        return CallResult(placed=True, provider_call_id=str(body["id"]),
                          error="", raw=body)
    return CallResult(placed=False,
                      error=f"dispatch failed: HTTP {status} {body.get('error') or body}"[:200],
                      raw=body)
