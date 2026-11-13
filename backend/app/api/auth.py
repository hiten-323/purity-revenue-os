"""Authentication for internal state-changing API requests."""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request


def _configured_secret() -> str:
    return (os.getenv("API_ADMIN_SECRET") or os.getenv("GATEWAY_ADMIN_SECRET") or "").strip()


def require_api_admin(request: Request) -> None:
    """Require an explicitly configured admin secret for mutating API routes."""
    secret = _configured_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="state-changing API disabled: set API_ADMIN_SECRET",
        )

    provided = (
        request.headers.get("x-api-admin-secret")
        or request.headers.get("x-admin-secret")
        or ""
    ).strip()
    authorization = request.headers.get("authorization", "")
    if not provided and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTPException(
            status_code=503,
            detail="state-changing API disabled: invalid admin secret",
        )


def is_protected_webhook_path(path: str) -> bool:
    return (
        path.startswith("/api/v1/webhooks/")
        or path == "/api/v1/whatsapp/webhook"
        or path == "/api/v1/b2b/vapi-webhook"
        or path == "/api/v1/inbound/lead-webhook"
    )


def _configured_outreach_status_token() -> str:
    return (
        os.getenv("OUTREACH_STATUS_API_TOKEN")
        or os.getenv("OUTREACH_STATUS_TOKEN")
        or os.getenv("API_ADMIN_SECRET")
        or ""
    ).strip()


def is_status_auth_configured() -> bool:
    """Check if the outreach status authentication token is configured."""
    return bool(_configured_outreach_status_token())


def require_outreach_status_token(request: Request) -> None:
    """Require OUTREACH_STATUS_API_TOKEN or API_ADMIN_SECRET for read-only status access.
    
    Supports:
      - Authorization: Bearer <token>
      - X-Status-Token: <token>
      - X-Api-Admin-Secret: <token>
    """
    secret = _configured_outreach_status_token()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="outreach status API disabled: set OUTREACH_STATUS_API_TOKEN or API_ADMIN_SECRET",
        )

    provided = (
        request.headers.get("x-status-token")
        or request.headers.get("x-api-admin-secret")
        or request.headers.get("x-admin-secret")
        or ""
    ).strip()
    
    authorization = request.headers.get("authorization", "").strip()
    if not provided and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()

    if not provided:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: missing Authorization Bearer token or X-Status-Token header",
        )

    if not hmac.compare_digest(provided, secret):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: invalid status API token",
        )
