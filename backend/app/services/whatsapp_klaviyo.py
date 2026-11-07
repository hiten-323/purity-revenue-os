"""
Klaviyo-as-BSP WhatsApp transport (28 Oct 2026). Every flag defaults OFF.

Klaviyo has no "send one WhatsApp message" API. Messages leave Klaviyo only
from a campaign or a flow, using a Meta-APPROVED template. So the server does
not send; it EMITS ONE EVENT per approved lead ("PB WhatsApp Outreach Ready"),
and a metric-triggered Klaviyo flow whose only action is a WhatsApp message
(approved template) sends it immediately. Pacing therefore stays on the server
(whatsapp_pacer), one event at a time. Reached only via
whatsapp_sender.send_whatsapp, which checks consent and the approved draft first.

Off unless WHATSAPP_PROVIDER=klaviyo AND KLAVIYO_WA_ENABLED=1 AND
KLAVIYO_WA_FLOW_LIVE=1 (set only after Hiten confirms the flow is Live and the
template is APPROVED). AiSensy stays off and is never called from here.
Never logs a full phone number or the API key.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger("whatsapp_klaviyo")
API = "https://a.klaviyo.com/api"
REVISION = "2025-07-15"
METRIC = os.getenv("KLAVIYO_WA_METRIC", "PB WhatsApp Outreach Ready")


def mask(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return ("XXXXXX" + d[-4:]) if d else "-"


def e164_in(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("91") and len(d) == 12:
        d = d[2:]
    d = d.lstrip("0")
    if len(d) == 10 and d[0] in "6789":
        return "+91" + d
    return ""


def _on(name: str) -> bool:
    return (os.getenv(name, "0") or "0").strip().lower() in {"1", "true", "yes", "on"}


def config_status() -> tuple[bool, str]:
    prov = (os.getenv("WHATSAPP_PROVIDER") or "").strip().lower()
    if not prov:
        try:
            from dotenv import dotenv_values
            from pathlib import Path
            prov = (dotenv_values(Path(__file__).resolve().parents[2] / ".env").get("WHATSAPP_PROVIDER") or "klaviyo").strip().lower()
        except Exception:
            prov = "klaviyo"
    if prov != "klaviyo":
        return False, f"WHATSAPP_PROVIDER is not 'klaviyo' (is {prov!r})"

    def _val(k):
        v = os.getenv(k)
        if v is None and not os.getenv("PYTEST_CURRENT_TEST"):
            try:
                from dotenv import dotenv_values
                from pathlib import Path
                v = dotenv_values(Path(__file__).resolve().parents[2] / ".env").get(k, "0")
            except Exception:
                v = "0"
        return (v or "0").strip().lower() in {"1", "true", "yes", "on"}

    if not _val("KLAVIYO_WA_ENABLED"):
        return False, "KLAVIYO_WA_ENABLED is not 1 — Klaviyo WhatsApp is off"
    
    k = os.getenv("KLAVIYO_PRIVATE_KEY")
    if not k:
        try:
            from dotenv import dotenv_values
            from pathlib import Path
            k = dotenv_values(Path(__file__).resolve().parents[2] / ".env").get("KLAVIYO_PRIVATE_KEY", "")
        except Exception:
            pass
    if not (k or "").strip():
        return False, "KLAVIYO_PRIVATE_KEY missing"

    if not _val("KLAVIYO_WA_FLOW_LIVE"):
        return False, ("KLAVIYO_WA_FLOW_LIVE is not 1 — set only after the flow is Live, "
                       "the sender number is APPROVED and the template is APPROVED by Meta")
    return True, f"klaviyo metric={METRIC!r}"


@dataclass(frozen=True)
class SendResult:
    status: str                # queued_to_klaviyo | blocked | not_configured | failed
    reason: str = ""
    message_id: str = ""       # our unique_id; delivery comes from Klaviyo later
    provider_accepted: bool = False
    delivery_confirmed: bool = False


def emit(lead, draft_id: str, template_key: str, params: dict, timeout: float = 15.0,
         consent: "CallConsent | None" = None) -> SendResult:
    ok, why = config_status()
    if not ok:
        return SendResult("not_configured", why)
    phone = e164_in(getattr(lead, "consent_phone", None) or getattr(lead, "whatsapp_number", None) or "")
    if not phone:
        return SendResult("blocked", "no valid Indian mobile bound to consent")
    if consent is None:
        return SendResult("blocked", "no AI-call consent record; Klaviyo subscription not checked")
    sub_ok, sub_why = ensure_subscribed(lead, phone, consent, timeout=timeout)
    if not sub_ok:
        return SendResult("blocked", sub_why)
    unique_id = f"pb-wa-{lead.id}-{draft_id}"      # Klaviyo de-dupes on unique_id
    body = {"data": {"type": "event", "attributes": {
        "unique_id": unique_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "properties": {"template_key": template_key, "draft_id": draft_id,
                       **event_params(params)},
        "metric": {"data": {"type": "metric", "attributes": {"name": METRIC}}},
        "profile": {"data": {"type": "profile", "attributes": {
            "phone_number": phone, "external_id": f"lead-{lead.id}"}}},
    }}}
    headers = {"Authorization": f"Klaviyo-API-Key {_key()}",
               "revision": REVISION, "accept": "application/vnd.api+json",
               "content-type": "application/vnd.api+json"}
    try:
        r = httpx.post(f"{API}/events", json=body, headers=headers, timeout=timeout)
    except Exception as e:  # unknown: treated as sent by the pacer (never retried blindly)
        log.warning("klaviyo wa emit lead %s to %s: transport error %s", lead.id, mask(phone), type(e).__name__)
        return SendResult("failed", f"transport error {type(e).__name__}", unique_id)
    if r.status_code == 202:
        log.info("klaviyo wa event queued lead %s to %s", lead.id, mask(phone))
        return SendResult("queued_to_klaviyo", "event accepted; flow sends the approved template",
                          unique_id, provider_accepted=True)
    log.warning("klaviyo wa emit lead %s HTTP %s", lead.id, r.status_code)
    return SendResult("failed", f"HTTP {r.status_code}", unique_id)


def send_one_recipient(lead, db, *, draft_id: str, template_key: str = "",
                       params: dict | None = None, timeout: float = 15.0) -> SendResult:
    """One Klaviyo event for one allow-listed lead.

    Does not read KLAVIYO_WA_FLOW_LIVE, does not change a flow, and does not
    subscribe or enroll any other profile. A 202 is provider acceptance of
    the event, not proof the WhatsApp was delivered or read.
    """
    from app.services.outbound_cohort import whatsapp_refusal
    refused = whatsapp_refusal(lead, db)
    if refused:
        return SendResult("blocked", refused)
    phone = e164_in(getattr(lead, "consent_phone", None) or getattr(lead, "whatsapp_number", None) or "")
    if not phone:
        return SendResult("blocked", "no valid Indian mobile bound to consent")
    if not (draft_id or "").strip():
        return SendResult("blocked", "a single-recipient send needs one draft id")
    try:
        _profile_id, can_receive, state = wa_status(phone, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return SendResult("blocked", "subscription lookup failed (%s); not sent" % type(exc).__name__)
    if not can_receive:
        return SendResult("blocked", "provider subscription %s; not sent" % (state or "not subscribed"))
    unique_id = "pb-wa-%s-%s" % (lead.id, draft_id)
    body = {"data": {"type": "event", "attributes": {
        "unique_id": unique_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "properties": {"template_key": template_key, "draft_id": draft_id,
                       **event_params(params or {})},
        "metric": {"data": {"type": "metric", "attributes": {"name": METRIC}}},
        "profile": {"data": {"type": "profile", "attributes": {
            "phone_number": phone, "external_id": "lead-%s" % lead.id}}},
    }}}
    try:
        response = httpx.post("%s/events" % API, json=body, headers=_headers(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.warning("klaviyo wa single lead %s transport error %s", lead.id, type(exc).__name__)
        return SendResult("failed", "transport error %s" % type(exc).__name__, unique_id)
    if response.status_code == 202:
        log.info("klaviyo wa single event accepted lead %s", lead.id)
        return SendResult("queued_to_klaviyo", "event accepted for one profile; delivery not confirmed",
                          unique_id, provider_accepted=True)
    log.warning("klaviyo wa single lead %s HTTP %s", lead.id, response.status_code)
    return SendResult("failed", "HTTP %s" % response.status_code, unique_id)


# ---------------------------------------------------------------------------
# 29 Oct 2026 (Hiten, 5:47 PM IST: "Yes, record call consent in Klaviyo
# automatically"). Two rules added after lead 1043:
#
# 1. The event carries ONLY what the approved template may use (name, company).
#    The draft's review copy (review_text, class, flags) stays on the server for
#    review; it never travels to Klaviyo, so wrong copy can never reach a message.
# 2. Before the event is emitted, the profile must be SUBSCRIBED to WhatsApp
#    marketing in Klaviyo. If it is not, and the lead's consent was recorded on
#    an AI call (EXPLICIT, source AI_CALL_WHATSAPP_REQUEST, a real past time, a
#    call id), the server subscribes it (historical import with the consent
#    time; consent time, source and call id stored as profile properties) and
#    holds the send. The next pacer tick re-reads the profile and emits only
#    once Klaviyo reports can_receive. Never inferred: no call consent, no
#    subscription. KLAVIYO_WA_AUTO_SUBSCRIBE=0 turns the subscribe step off.
# ---------------------------------------------------------------------------
EVENT_PARAM_KEYS = ("name", "company")
SUBSCRIBE_RETRY_MIN = 20          # minutes between subscribe attempts per lead
SUBSCRIBE_MAX_TRIES = 3
_ATTEMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "data", "klaviyo_wa_subscribe_attempts.json")


def event_params(params: dict) -> dict:
    out = {}
    for k in EVENT_PARAM_KEYS:
        v = (params or {}).get(k)
        out[k] = ("" if v is None else str(v))[:120]
    return out


@dataclass(frozen=True)
class CallConsent:
    consented_at: datetime            # aware UTC
    call_id: str
    source: str = "AI_CALL"


def call_consent(db, lead) -> tuple["CallConsent | None", str]:
    """The consent record from the AI call, or (None, why). Never inferred."""
    from datetime import timedelta
    if (getattr(lead, "consent_status", "") or "").upper() != "EXPLICIT":
        return None, "consent is not EXPLICIT"
    if (getattr(lead, "consent_source", "") or "") != "AI_CALL_WHATSAPP_REQUEST":
        return None, "consent was not recorded on an AI call"
    if getattr(lead, "do_not_call", False):
        return None, "lead is do_not_call"
    if getattr(lead, "whatsapp_verified", None) is False:
        return None, "number is marked as not on WhatsApp"
    ts = getattr(lead, "consent_timestamp", None)
    if ts is None:
        return None, "no consent timestamp"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts >= datetime.now(timezone.utc):
        return None, "consent timestamp is in the future"
    # A later WHATSAPP_CONSENT_UNBOUND (founder to confirm) overrides older consent.
    try:
        from app.models.models import WorkflowEvent
        naive_ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        unb = (db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id,
               WorkflowEvent.event_type == "WHATSAPP_CONSENT_UNBOUND",
               WorkflowEvent.occurred_at > naive_ts).first())
    except Exception:  # noqa: BLE001
        return None, "could not check for a later unbound opt-in"
    if unb is not None:
        return None, "a later opt-in is unbound; founder must confirm"
    call_id = ""
    try:
        from sqlalchemy import text
        naive = ts.astimezone(timezone.utc).replace(tzinfo=None)
        row = db.execute(text(
            "SELECT call_id FROM call_results WHERE lead_id=:l AND started_at<=:t "
            "AND (ended_at IS NULL OR ended_at>=:t0) ORDER BY started_at DESC LIMIT 1"),
            {"l": lead.id, "t": naive, "t0": naive - timedelta(minutes=10)}).first()
        call_id = (row[0] if row else "") or ""
    except Exception:  # noqa: BLE001
        call_id = ""
    if not call_id:
        try:
            from app.models.models import WorkflowEvent
            ev = (db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id,
                                                 WorkflowEvent.event_type == "WHATSAPP_CONSENT_RECORDED")
                  .order_by(WorkflowEvent.id.desc()).first())
            pl = ev.payload if ev is not None else None
            if isinstance(pl, str):
                import json as _j
                pl = _j.loads(pl)
            m = re.search(r"call_ref:\s*([\w.-]+)", str((pl or {}).get("evidence", "")))
            call_id = m.group(1) if m else ""
        except Exception:  # noqa: BLE001
            call_id = ""
    if not call_id:
        return None, "no AI call id for the consent"
    return CallConsent(consented_at=ts.astimezone(timezone.utc), call_id=str(call_id)), "ok"


def _key() -> str:
    k = os.getenv("KLAVIYO_PRIVATE_KEY")
    if not k:
        try:
            from dotenv import dotenv_values
            from pathlib import Path
            k = dotenv_values(Path(__file__).resolve().parents[2] / ".env").get("KLAVIYO_PRIVATE_KEY", "")
        except Exception:
            pass
    return (k or "").strip()

def _headers() -> dict:
    return {"Authorization": f"Klaviyo-API-Key {_key()}",
            "revision": REVISION, "accept": "application/vnd.api+json",
            "content-type": "application/vnd.api+json"}


def wa_status(phone: str, timeout: float = 15.0) -> tuple[str, bool, str]:
    """(profile_id, can_receive, consent) for this number; ('', False, 'NO_PROFILE') if none."""
    r = httpx.get(f"{API}/profiles", headers=_headers(), timeout=timeout,
                  params={"filter": f'equals(phone_number,"{phone}")',
                          "additional-fields[profile]": "subscriptions"})
    if r.status_code != 200:
        raise RuntimeError(f"profile lookup HTTP {r.status_code}")
    data = (r.json() or {}).get("data") or []
    if not data:
        return "", False, "NO_PROFILE"
    p = data[0]
    m = (((p.get("attributes") or {}).get("subscriptions") or {}).get("whatsapp") or {}).get("marketing") or {}
    return p.get("id", ""), bool(m.get("can_receive")) and m.get("consent") == "SUBSCRIBED", m.get("consent") or "?"


def _attempts(lead_id) -> list:
    try:
        import json as _j
        return list(_j.load(open(_ATTEMPTS)).get(str(lead_id), []))
    except Exception:  # noqa: BLE001
        return []


def _note_attempt(lead_id) -> None:
    import json as _j
    try:
        d = _j.load(open(_ATTEMPTS))
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault(str(lead_id), []).append(datetime.now(timezone.utc).isoformat())
    os.makedirs(os.path.dirname(_ATTEMPTS), exist_ok=True)
    with open(_ATTEMPTS, "w") as f:
        _j.dump(d, f, indent=1)


def subscribe(lead, phone: str, consent: CallConsent, profile_id: str = "",
              timeout: float = 15.0) -> tuple[bool, str]:
    """Record the call consent in Klaviyo: profile properties, then the WhatsApp
    marketing subscription (historical import with the consent time). No list
    relationship on the job (the 28 Oct list+consent job was accepted but never
    applied on 1043 or 550). Returns (submitted, why)."""
    at = consent.consented_at.isoformat()
    props = {"wa_consented_at": at, "wa_consent_source": consent.source,
             "wa_consent_call_id": consent.call_id, "lead_id": lead.id}
    prof = {"data": {"type": "profile", "attributes": {
        "phone_number": phone, "external_id": f"lead-{lead.id}", "properties": props}}}
    r = httpx.post(f"{API}/profile-import", json=prof, headers=_headers(), timeout=timeout)
    if r.status_code not in (200, 201):
        return False, f"profile update HTTP {r.status_code}"
    pid = profile_id or ((r.json() or {}).get("data") or {}).get("id", "")
    entry = {"type": "profile", "attributes": {"phone_number": phone, "subscriptions": {
        "whatsapp": {"marketing": {"consent": "SUBSCRIBED", "consented_at": at}}}}}
    if pid:
        entry["id"] = pid
    job = {"data": {"type": "profile-subscription-bulk-create-job", "attributes": {
        "custom_source": f"AI call {consent.call_id} (WhatsApp consent on the call)",
        "historical_import": True,
        "profiles": {"data": [entry]}}}}
    r = httpx.post(f"{API}/profile-subscription-bulk-create-jobs", json=job, headers=_headers(),
                   timeout=timeout)
    if r.status_code != 202:
        return False, f"subscribe HTTP {r.status_code}"
    log.info("klaviyo wa subscribe submitted lead %s to %s (call %s)", lead.id, mask(phone), consent.call_id)
    return True, "submitted"


def ensure_subscribed(lead, phone: str, consent: CallConsent, timeout: float = 15.0) -> tuple[bool, str]:
    """True only when Klaviyo already reports the number SUBSCRIBED + can_receive."""
    try:
        pid, ok, state = wa_status(phone, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return False, f"klaviyo subscription check failed ({type(e).__name__}); not sent"
    if ok:
        return True, "subscribed"
    if not _on_default("KLAVIYO_WA_AUTO_SUBSCRIBE", True):
        return False, f"klaviyo profile not subscribed to WhatsApp ({state}); auto-subscribe is off"
    tries = _attempts(lead.id)
    if len(tries) >= SUBSCRIBE_MAX_TRIES:
        return False, (f"klaviyo profile still {state} after {len(tries)} subscribe attempts; "
                       "needs a look in Klaviyo")
    if tries:
        last = datetime.fromisoformat(tries[-1])
        if (datetime.now(timezone.utc) - last).total_seconds() < SUBSCRIBE_RETRY_MIN * 60:
            return False, f"klaviyo subscription pending ({state}); waiting for Klaviyo to apply it"
    try:
        done, why = subscribe(lead, phone, consent, pid, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        done, why = False, f"transport error {type(e).__name__}"
    _note_attempt(lead.id)
    if not done:
        return False, f"klaviyo subscribe failed: {why}; not sent"
    return False, "klaviyo subscription submitted from the call consent; sends after Klaviyo confirms"


def _on_default(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}
