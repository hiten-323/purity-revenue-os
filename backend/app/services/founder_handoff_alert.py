"""Founder hand-off alerts for positive AI-call outcomes.

4 Nov 2026 (Hiten "stop,fix all,resume", audit item f): interested /
callback / meeting / catalogue asks went unnoticed (e.g. a meeting request on
2 Nov was never surfaced). Every such outcome is now journalled as an
internal alert for the founder.

SAFETY: sending is OFF unless FOUNDER_ALERT_SEND=1. With it off (default)
the alert is only written to data/founder_alerts/queue.jsonl and nothing
leaves the server. With it on, a Klaviyo metric event (FOUNDER_ALERT_METRIC,
default "PB Founder Lead Alert") is emitted on the founder's own profile; a
Klaviyo flow on that metric (with an approved template) delivers it to the
founder's WhatsApp. No message ever goes to the lead from here.
Phone numbers are masked in the journal and in logs.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
ALERT_OUTCOMES = {
    "INTERESTED": "is interested",
    "CALLBACK_REQUESTED": "asked for a callback",
    "MEETING_REQUESTED": "asked for a meeting/visit",
    "HUMAN_HANDOFF": "asked to speak with the team",
    "CATALOGUE_REQUESTED": "asked for the catalogue",
    "SEND_INFO_EMAIL": "asked for details/catalogue on email",
}
METRIC = os.getenv("FOUNDER_ALERT_METRIC", "PB Founder Lead Alert")
_BACKEND = Path(__file__).resolve().parents[2]
# 4 Nov 2026: under pytest never touch the live journal and never send (5 test rows had
# landed in the live queue). Tests that want a journal set FOUNDER_ALERT_QUEUE.
_UNDER_TEST = "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))
_DEFAULT_QUEUE = (Path(tempfile.gettempdir()) / f"founder_alerts_test_{os.getpid()}.jsonl") if _UNDER_TEST \
    else (_BACKEND / "data" / "founder_alerts" / "queue.jsonl")
QUEUE = Path(os.getenv("FOUNDER_ALERT_QUEUE", str(_DEFAULT_QUEUE)))


def mask(phone: str | None) -> str:
    d = "".join(c for c in str(phone or "") if c.isdigit())
    return ("…" + d[-4:]) if len(d) >= 4 else ("" if not d else "…")


def send_enabled() -> bool:
    if _UNDER_TEST and os.getenv("FOUNDER_ALERT_ALLOW_UNDER_TEST", "0") != "1":
        return False
    return os.getenv("FOUNDER_ALERT_SEND", "0").strip() == "1"


def build_text(lead, key: str, summary: str = "", callback_window: str = "") -> str:
    what = ALERT_OUTCOMES.get(key, key.lower())
    name = (getattr(lead, "company", None) or getattr(lead, "business_name", None) or getattr(lead, "name", None) or f"Lead {getattr(lead, 'id', '?')}").strip()
    city = (getattr(lead, "city", None) or "").strip()
    parts = [f"Purity Beans lead alert: {name}{', ' + city if city else ''} {what} on the AI call."]
    if callback_window:
        parts.append(f"When: {callback_window}.")
    if summary:
        parts.append(f"Note: {summary.strip()[:220]}")
    parts.append(f"Lead #{getattr(lead, 'id', '?')}, phone {mask(getattr(lead, 'phone', None))}. Please follow up.")
    return " ".join(parts)


def _already(lead_id, key: str, day: str) -> bool:
    if not QUEUE.exists():
        return False
    try:
        for line in QUEUE.read_text(encoding="utf-8").splitlines()[-2000:]:
            r = json.loads(line)
            if r.get("lead_id") == lead_id and r.get("key") == key and r.get("day") == day:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _emit_klaviyo(text: str, rec: dict) -> tuple[bool, str]:
    import requests
    k = os.getenv("KLAVIYO_PRIVATE_KEY", "")
    if not k:
        try:
            from dotenv import dotenv_values
            k = dotenv_values(_BACKEND / ".env").get("KLAVIYO_PRIVATE_KEY", "") or ""
        except Exception:  # noqa: BLE001
            k = ""
    phone = os.getenv("FOUNDER_ALERT_PHONE", "").strip()
    if not k or not phone:
        return False, "klaviyo key or FOUNDER_ALERT_PHONE missing"
    body = {"data": {"type": "event", "attributes": {
        "properties": {"alert_text": text, "lead_id": rec["lead_id"], "outcome": rec["key"]},
        "metric": {"data": {"type": "metric", "attributes": {"name": METRIC}}},
        "profile": {"data": {"type": "profile", "attributes": {"phone_number": phone}}},
        "unique_id": f"founder-alert-{rec['lead_id']}-{rec['key']}-{rec['day']}",
    }}}
    r = requests.post("https://a.klaviyo.com/api/events/", json=body, timeout=15, headers={
        "Authorization": f"Klaviyo-API-Key {k}", "revision": "2025-07-15",
        "Content-Type": "application/vnd.api+json", "accept": "application/vnd.api+json"})
    return r.status_code in (200, 202), f"http {r.status_code}"


def on_call_outcome(lead, db=None, key: str = "", *, summary: str = "", callback_window: str = "",
                    now: datetime | None = None) -> dict | None:
    key = (key or "").strip().upper()
    if key not in ALERT_OUTCOMES:
        return None
    now = now or datetime.now(IST)
    day = now.astimezone(IST).date().isoformat()
    lead_id = getattr(lead, "id", None)
    if _already(lead_id, key, day):
        return None
    text = build_text(lead, key, summary, callback_window)
    rec = {"ts": now.astimezone(IST).isoformat(), "day": day, "lead_id": lead_id, "key": key,
           "text": text, "status": "QUEUED", "sent": False}
    if send_enabled():
        try:
            ok, why = _emit_klaviyo(text, rec)
            rec.update(status="SENT" if ok else "SEND_FAILED", sent=ok, why=why)
        except Exception as e:  # noqa: BLE001
            rec.update(status="SEND_FAILED", why=type(e).__name__)
    try:
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with QUEUE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        log.warning("founder alert journal write failed lead=%s", lead_id)
    log.info("founder alert %s lead=%s key=%s", rec["status"], lead_id, key)
    return rec
