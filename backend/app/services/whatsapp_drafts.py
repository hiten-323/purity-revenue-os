"""
Founder approval for WhatsApp drafts (28 Oct 2026). No approved draft -> no send.

Store: backend/data/whatsapp_drafts.json
  {draft_id: {lead_id, template_key, params, status, created_at,
              approved_by, approved_at, sent_at, provider_ref}}
status: PENDING -> APPROVED -> SENT   (or REJECTED)
Only scripts/whatsapp_drafts.py --approve flips PENDING to APPROVED, and only
on the founder's own words.
"""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

STORE = Path(os.getenv("WA_DRAFTS_FILE",
             str(Path(__file__).resolve().parents[2] / "data" / "whatsapp_drafts.json")))


def _load() -> dict:
    try:
        return json.loads(STORE.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, default=str))
    os.replace(tmp, STORE)


def _locked(fn):
    def w(*a, **k):
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with open(str(STORE) + ".lock", "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            return fn(*a, **k)
    return w


def all_drafts() -> dict:
    return _load()


@_locked
def create(lead_id: int, template_key: str, params: dict) -> str:
    d = _load()
    for k, v in d.items():
        if v.get("lead_id") == lead_id and v.get("status") in ("PENDING", "APPROVED"):
            return k
    did = uuid.uuid4().hex[:10]
    d[did] = {"lead_id": lead_id, "template_key": template_key, "params": params,
              "status": "PENDING", "created_at": datetime.utcnow().isoformat()}
    _save(d)
    return did


@_locked
def approve(draft_id: str, by: str) -> dict:
    d = _load()
    if draft_id not in d or d[draft_id]["status"] != "PENDING":
        raise ValueError("draft not found or not PENDING")
    if not (by or "").strip():
        raise ValueError("approver required")
    d[draft_id].update(status="APPROVED", approved_by=by.strip(),
                       approved_at=datetime.utcnow().isoformat())
    _save(d)
    return d[draft_id]


@_locked
def auto_approve(draft_id: str, by: str, template_key: str = "") -> dict:
    """29 Oct: only app.services.auto_send calls this, after its gates, while
    OUTREACH_AUTO_SEND=1. Marks who approved; never sends."""
    from app.services import auto_send
    if not auto_send.enabled():
        raise PermissionError("OUTREACH_AUTO_SEND is off")
    d = _load()
    if draft_id not in d or d[draft_id]["status"] != "PENDING":
        raise ValueError("draft not found or not PENDING")
    upd = dict(status="APPROVED", approved_by=by, approved_at=datetime.utcnow().isoformat(), auto=True)
    if template_key:
        upd["template_key"] = template_key
    d[draft_id].update(**upd)
    _save(d)
    return d[draft_id]


@_locked
def reject(draft_id: str) -> None:
    d = _load()
    if draft_id in d and d[draft_id]["status"] in ("PENDING", "APPROVED"):
        d[draft_id]["status"] = "REJECTED"
        _save(d)


def approved_for(draft_id: str, lead_id: int):
    v = _load().get(draft_id or "")
    if v and v.get("status") == "APPROVED" and v.get("lead_id") == lead_id:
        return v
    return None


@_locked
def mark_sent(draft_id: str, provider_ref: str) -> None:
    d = _load()
    if draft_id in d:
        d[draft_id].update(status="SENT", sent_at=datetime.utcnow().isoformat(),
                           provider_ref=provider_ref)
        _save(d)


def next_approved():
    items = [(k, v) for k, v in _load().items() if v.get("status") == "APPROVED"]
    items.sort(key=lambda kv: kv[1].get("approved_at") or "")
    return items
