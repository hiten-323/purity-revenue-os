"""Klaviyo -> AiSensy WhatsApp bridge.

Security model:
- AiSensy API key is read only from AISENSY_API_KEY in the process environment.
- Klaviyo webhook requests must carry X-Connector-Secret matching
  KLAVIYO_WHATSAPP_WEBHOOK_SECRET when configured.
- No credentials are accepted in the request body.
- Idempotency keys are persisted locally so Klaviyo retries cannot duplicate sends.

The bridge intentionally does not mutate Klaviyo flows. A Klaviyo flow can call
POST /api/v1/whatsapp/send as a webhook action. The flow remains the source of
truth for timing, filters and suppression; this service is only the transport.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

# Endpoint removed: whatsapp_evolution is the only transport.
DB_PATH = os.getenv("WHATSAPP_DB_PATH", os.path.join(os.path.dirname(__file__), "whatsapp_connector.db"))
REQUEST_TIMEOUT = float(os.getenv("WHATSAPP_TIMEOUT_SECONDS",
                                  os.getenv("AISENSY_TIMEOUT_SECONDS", "15")))

app = FastAPI(title="Purity Beans Klaviyo WhatsApp Connector", version="1.0.0")
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS whatsapp_sends (
            idempotency_key TEXT PRIMARY KEY,
            destination TEXT NOT NULL,
            campaign_name TEXT NOT NULL,
            status TEXT NOT NULL,
            provider_status INTEGER,
            provider_response TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    return conn


# The lead database, which is where consent actually lives. The connector keeps
# its own send log, but it must not become a second authority on who may be
# messaged — that is the failure this codebase keeps rediscovering.
LEADS_DB_PATH = os.getenv(
    "PURITY_LEADS_DB_PATH", os.path.join(os.path.dirname(__file__), "purity_beans.db")
)


def _consent_ok(destination: str) -> tuple[bool, str]:
    """Meta requires opt-in before a template message. So does this connector.

    The rule is whatsapp_sender.consent_check -- called, not restated, so this
    path and the main sender cannot drift. It is fed the lead row this number
    belongs to; if the rule cannot be imported or the row cannot be read, we
    refuse, because a send path that cannot reach the consent rule has not
    satisfied it.

    Matching is on the last 10 digits across phone, whatsapp_number and
    consent_phone: the table stores numbers in several formats
    (+91-98765-43210, 919876543210, 9876543210), and a number the business
    gave us for WhatsApp lives in whatsapp_number/consent_phone, not phone.
    """
    try:
        from types import SimpleNamespace

        from app.services.whatsapp_sender import consent_check
    except Exception as exc:  # noqa: BLE001 - fail closed, never open
        return False, f"consent rule unavailable ({exc.__class__.__name__}); refusing to send"

    digits = "".join(ch for ch in destination if ch.isdigit())[-10:]
    if len(digits) < 10:
        return False, "destination is not a full 10-digit Indian number"

    def norm(col: str) -> str:
        return f"replace(replace(replace(coalesce({col},''),'-',''),' ',''),'+','')"

    try:
        conn = sqlite3.connect(f"file:{LEADS_DB_PATH}?mode=ro", uri=True, timeout=10)
        rows = conn.execute(
            "SELECT consent_status, do_not_call, company, consent_phone, "
            "whatsapp_number, phone FROM b2b_leads "
            f"WHERE {norm('phone')} LIKE ? OR {norm('whatsapp_number')} LIKE ? "
            f"OR {norm('consent_phone')} LIKE ?",
            (f"%{digits}",) * 3,
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"lead lookup failed ({exc.__class__.__name__}); refusing to send"

    if not rows:
        return False, "no lead on record for this number; consent cannot be established"
    # Any record of this number asking us to stop wins over any other record's yes.
    for r in rows:
        if r[1]:
            return False, f"{r[2] or 'lead'} is marked do-not-call"

    def ends(v) -> bool:
        return "".join(ch for ch in (v or "") if ch.isdigit())[-10:] == digits

    # The lead that opted in on this exact number, if there is one.
    row = next((r for r in rows if ends(r[3])), rows[0])
    lead = SimpleNamespace(consent_status=row[0], do_not_call=bool(row[1]),
                           consent_phone=row[3], whatsapp_number=row[4], phone=row[5],
                           status="")  # template sends need opt-in, not a reply
    ok, why = consent_check(lead)
    if not ok:
        return False, why
    # consent_check proves consent covers the lead's current number; the
    # message must also be going to that number.
    if not ends(row[4] or row[5]):
        return False, "destination is not the number this lead's consent covers"
    return True, (row[0] or "").upper()


def _secret_ok(provided: str | None) -> bool:
    expected = os.getenv("KLAVIYO_WHATSAPP_WEBHOOK_SECRET", "")
    if not expected:
        # Explicitly fail closed in production if no secret is configured.
        return os.getenv("ENVIRONMENT", "development").lower() != "production"
    return bool(provided) and hmac.compare_digest(provided, expected)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if value.strip().startswith("+"):
        return "+" + digits
    # AiSensy accepts Indian numbers without +, but the connector standardizes
    # all destinations to E.164-style +country-code form.
    if len(digits) == 10:
        return "+91" + digits
    if digits.startswith("91") and len(digits) == 12:
        return "+" + digits
    return "+" + digits


class WhatsAppSend(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    campaign_name: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=8, max_length=30)
    user_name: str = Field(default="Customer", max_length=200)
    template_params: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(default="Klaviyo", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=50)
    media_url: str | None = None
    media_filename: str | None = None

    @field_validator("destination")
    @classmethod
    def phone(cls, v: str) -> str:
        normalized = _normalize_phone(v)
        if len(normalized) < 11 or len(normalized) > 16:
            raise ValueError("destination must be a valid phone number")
        return normalized


def _provider_payload(req: WhatsAppSend, api_key: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "apiKey": api_key,
        "campaignName": req.campaign_name,
        "destination": req.destination,
        "userName": req.user_name,
        "source": req.source,
        "templateParams": req.template_params,
        "tags": req.tags,
        "attributes": req.attributes,
    }
    if req.media_url:
        payload["media"] = {
            "url": req.media_url,
            "filename": req.media_filename or "media",
        }
    return payload


def _request_fingerprint(req: WhatsAppSend) -> str:
    body = req.model_dump(exclude_none=True)
    body.pop("idempotency_key", None)
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@app.get("/api/v1/whatsapp/health")
def health() -> dict[str, Any]:
    from app.services import whatsapp_aisensy as _t
    configured = _t.config_status()[0]
    secret_configured = bool(os.getenv("KLAVIYO_WHATSAPP_WEBHOOK_SECRET"))
    return {
        "service": "klaviyo-aisensy-whatsapp",
        "status": "ready" if configured and secret_configured else "configuration_required",
        "aisensy_configured": configured,
        "webhook_secret_configured": secret_configured,
        "provider_endpoint": _t.base_url(),
        "integration": _t.integration(),
    }


@app.post("/api/v1/whatsapp/send")
def send_whatsapp(req: WhatsAppSend, x_connector_secret: str | None = Header(default=None)) -> dict[str, Any]:
    if not _secret_ok(x_connector_secret):
        raise HTTPException(status_code=401, detail="invalid connector secret")

    api_key = os.getenv("AISENSY_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="AISENSY_API_KEY is not configured")

    # Consent is checked here, not assumed upstream. A caller holding the
    # connector secret is authenticated, not authorised: the secret proves the
    # request came from our own integration, and says nothing about whether the
    # business at the other end agreed to be messaged.
    allowed, why = _consent_ok(req.destination)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"consent gate: {why}")

    fingerprint = _request_fingerprint(req)
    with _db_lock:
        conn = _db()
        row = conn.execute(
            "SELECT status, provider_status, provider_response FROM whatsapp_sends WHERE idempotency_key=?",
            (req.idempotency_key,),
        ).fetchone()
        conn.close()
    if row:
        return {
            "status": "duplicate_suppressed",
            "idempotency_key": req.idempotency_key,
            "previous_status": row[0],
            "provider_status": row[1],
        }

    # Transport is Evolution API in Meta Cloud API mode, the one place a
    # WhatsApp message leaves this system. This used to POST to AiSensy itself,
    # which made it the second of three transports — and it was the one that
    # shipped with no consent check at all. The idempotency ledger below is
    # kept; only the socket changed.
    from app.services import whatsapp_aisensy as transport

    result = transport.send_template(
        req.destination, req.campaign_name,
        params=list(getattr(req, "template_params", None) or []),
        timeout=REQUEST_TIMEOUT)
    text = (result.reason or "")[:4000]
    if result.status == "sent":
        status, provider_status = "sent", 200
        text = (result.response or result.reason)[:4000]
    elif result.status in ("not_configured", "blocked"):
        status, provider_status = "provider_rejected", None
    else:
        status, provider_status = "provider_error", None

    now = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn = _db()
        conn.execute(
            "INSERT OR IGNORE INTO whatsapp_sends VALUES (?, ?, ?, ?, ?, ?, ?)",
            (req.idempotency_key, req.destination, req.campaign_name, status, provider_status, text, now),
        )
        conn.commit()
        conn.close()

    if status == "provider_error":
        raise HTTPException(status_code=502, detail="AiSensy request failed")
    if status == "provider_rejected":
        raise HTTPException(status_code=502, detail={"provider_status": provider_status, "provider_response": text})

    return {
        "status": "sent",
        "idempotency_key": req.idempotency_key,
        "destination": req.destination,
        "campaign_name": req.campaign_name,
        "provider_status": provider_status,
        "fingerprint": fingerprint,
    }


@app.post("/api/v1/whatsapp/status")
def provider_status(payload: dict[str, Any], x_provider_secret: str | None = Header(default=None)) -> dict[str, str]:
    """Optional status callback endpoint.

    AiSensy callback formats can vary by account/configuration, so the bridge
    stores the raw event only after the shared callback secret is configured.
    It deliberately does not pretend to translate an unknown provider schema
    into Klaviyo events.
    """
    expected = os.getenv("WHATSAPP_STATUS_WEBHOOK_SECRET", "")
    if not expected or not x_provider_secret or not hmac.compare_digest(x_provider_secret, expected):
        raise HTTPException(status_code=401, detail="invalid provider callback secret")
    with _db_lock:
        conn = _db()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS whatsapp_callbacks (received_at TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO whatsapp_callbacks VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
        conn.close()
    return {"status": "accepted"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("whatsapp_connector:app", host="0.0.0.0", port=int(os.getenv("WHATSAPP_PORT", "8091")), reload=False)
