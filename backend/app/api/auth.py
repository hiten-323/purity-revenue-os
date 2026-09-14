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
    )
