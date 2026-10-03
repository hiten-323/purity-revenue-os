"""System alerts to the founder (email to Hiten via the same Zoho SMTP path
as the daily brief). For operational emergencies only: provider credit
exhausted, voice agent wedged, auto-dispatch paused. Never goes to a lead.

Rate limited per alert key (FOUNDER_ALERT_MIN_INTERVAL_MIN, default 60) using
a small state file so a failing provider cannot flood the inbox. Never raises.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

FOUNDER_EMAIL = "hitenjain.12@gmail.com"
_LOCK = threading.Lock()


def _state_path() -> Path:
    p = os.getenv("FOUNDER_ALERT_STATE_FILE")
    if p:
        return Path(p)
    return Path(__file__).resolve().parents[2] / "logs" / "founder_alerts_state.json"


def _min_interval() -> timedelta:
    try:
        return timedelta(minutes=max(1, int(os.getenv("FOUNDER_ALERT_MIN_INTERVAL_MIN", "60"))))
    except ValueError:
        return timedelta(minutes=60)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _smtp_send(subject: str, body: str) -> dict:
    # The transport lives in founder_brief (the one founder-only mail path the
    # send-chokepoint test allows); this module never opens SMTP itself.
    from app.services.founder_brief import send_founder_system_email
    return send_founder_system_email(subject, body)


def send_system_alert(kind: str, detail: str, *, key: str | None = None,
                      now: datetime | None = None, sender=None) -> dict:
    """Email the founder a system alert unless the same key fired recently."""
    kind = (kind or "SYSTEM").strip().upper()[:60]
    key = (key or kind)[:120]
    now = now or datetime.utcnow()
    path = _state_path()
    with _LOCK:
        state = _load(path)
        last = state.get(key)
        if last:
            try:
                if now - datetime.fromisoformat(last) < _min_interval():
                    return {"sent": False, "reason": "rate_limited", "key": key, "last": last}
            except ValueError:
                pass
        subject = f"[Purity ALERT] {kind.replace('_', ' ').title()}"
        body = (f"System alert: {kind}\n"
                f"Time (UTC): {now.isoformat(timespec='seconds')}Z\n\n"
                f"{(detail or '').strip()[:4000]}\n\n"
                "Automatic dispatch stays paused until you resume it.\n"
                "-- Purity Revenue OS (automated system alert)")
        try:
            res = (sender or _smtp_send)(subject, body)
        except Exception as exc:  # noqa: BLE001
            log.error("founder alert %s failed: %s", key, exc)
            return {"sent": False, "reason": f"smtp error: {exc}", "key": key}
        if res.get("sent"):
            state[key] = now.isoformat()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(state, indent=1), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                log.warning("founder alert state write failed: %s", exc)
        log.warning("founder alert %s: %s", key, res)
        return {**res, "key": key}
