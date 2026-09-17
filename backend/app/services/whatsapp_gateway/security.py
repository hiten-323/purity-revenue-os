"""
Webhook authentication for the WhatsApp Gateway.

Production rule: fail closed.
  ENVIRONMENT=production  → KLAVIYO_WEBHOOK_SECRET is mandatory.
  Unsigned or missing-secret requests are rejected.

Development rule: if secrets are unset, reject as well. A webhook without a
configured secret is never safe to accept.

Supported verification modes (best-effort, provider-agnostic):
  1. Shared secret header: X-Webhook-Secret / X-Klaviyo-Webhook-Secret /
     X-AiSensy-Webhook-Secret must equal the configured secret.
  2. HMAC-SHA256 of raw body using the secret, compared to
     X-Webhook-Signature / X-Hub-Signature-256 as a base64 digest.

Replay protection:
  Klaviyo webhook signature scheme for custom HTTPS webhooks is not fully
  documented for our use case → marked UNKNOWN where timestamp is absent.
  If X-Webhook-Timestamp is present, reject if skew > WEBHOOK_MAX_SKEW_SECONDS.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Mapping, Optional

logger = logging.getLogger("whatsapp_gateway.security")

DEFAULT_MAX_SKEW_SECONDS = 300  # 5 minutes


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def is_production() -> bool:
    env = _env("ENVIRONMENT").lower() or _env("ENV").lower()
    return env in {"production", "prod"}


@dataclass(frozen=True)
class AuthResult:
    allowed: bool
    reason: str
    mode: str = "none"  # none | shared_secret | hmac | bypass_dev


def _constant_time_eq(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _extract_signature(headers: Mapping[str, str]) -> str:
    candidates = [
        headers.get("x-webhook-signature"),
        headers.get("x-hub-signature-256"),
        headers.get("x-klaviyo-signature"),
        headers.get("x-aisensy-signature"),
    ]
    for raw in candidates:
        if not raw:
            continue
        value = raw.strip()
        if value.lower().startswith("sha256="):
            value = value.split("=", 1)[1].strip()
        if value:
            return value
    return ""


def _extract_shared_secret_header(headers: Mapping[str, str]) -> str:
    for key in (
        "x-webhook-secret",
        "x-klaviyo-webhook-secret",
        "x-aisensy-webhook-secret",
        "x-gateway-secret",
    ):
        val = (headers.get(key) or "").strip()
        if val:
            return val
    auth = (headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _hmac_base64(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _check_timestamp(headers: Mapping[str, str]) -> Optional[str]:
    """Return error string if timestamp present and skewed; None if OK or absent."""
    ts_raw = (
        headers.get("x-webhook-timestamp")
        or headers.get("x-klaviyo-timestamp")
        or headers.get("x-request-timestamp")
        or ""
    ).strip()
    if not ts_raw:
        return None
    try:
        ts = int(float(ts_raw))
        if ts > 10_000_000_000:
            ts = ts // 1000
    except Exception:
        return "invalid webhook timestamp"
    skew = abs(int(time.time()) - ts)
    max_skew = int(_env("WEBHOOK_MAX_SKEW_SECONDS") or DEFAULT_MAX_SKEW_SECONDS)
    if skew > max_skew:
        return f"webhook timestamp skew {skew}s exceeds {max_skew}s"
    return None


def verify_webhook(
    *,
    provider: str,
    headers: Mapping[str, str],
    body: bytes,
    secret_env_var: str,
) -> AuthResult:
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    secret = _env(secret_env_var)

    if not secret:
        logger.error(
            "webhook_auth_fail provider=%s reason=missing_secret env=%s",
            provider,
            secret_env_var,
        )
        return AuthResult(
            allowed=False,
            reason=f"{secret_env_var} is required",
            mode="none",
        )

    ts_err = _check_timestamp(hdrs)
    if ts_err:
        return AuthResult(allowed=False, reason=ts_err, mode="timestamp")

    provided_secret = _extract_shared_secret_header(hdrs)
    if provided_secret and _constant_time_eq(provided_secret, secret):
        return AuthResult(allowed=True, reason="shared secret matched", mode="shared_secret")

    sig = _extract_signature(hdrs)
    if sig:
        expected = _hmac_base64(secret, body or b"")
        if _constant_time_eq(sig, expected):
            return AuthResult(allowed=True, reason="hmac matched", mode="hmac")

    return AuthResult(
        allowed=False,
        reason="missing or invalid webhook authentication",
        mode="none",
    )


def require_admin_secret(headers: Mapping[str, str]) -> AuthResult:
    """Protect internal gateway endpoints using GATEWAY_ADMIN_SECRET."""
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    secret = _env("GATEWAY_ADMIN_SECRET") or _env("KLAVIYO_WEBHOOK_SECRET")

    if not secret:
        return AuthResult(
            allowed=False,
            reason="GATEWAY_ADMIN_SECRET is required",
            mode="none",
        )

    provided = _extract_shared_secret_header(hdrs)
    if provided and _constant_time_eq(provided, secret):
        return AuthResult(allowed=True, reason="admin secret matched", mode="shared_secret")

    return AuthResult(allowed=False, reason="admin authentication failed", mode="none")
