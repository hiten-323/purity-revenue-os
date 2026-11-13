"""Last-mile validation for the operational settings write endpoint."""
from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse


ALLOWED_SETTINGS_KEYS = frozenset(
    {
        "CEREBRAS_API_KEY",
        "SENDER_EMAIL",
        "SENDER_NAME",
        "ZOHO_APP_PASSWORD",
        "SHOPIFY_STORE",
        "SHOPIFY_TOKEN",
        "SHOPIFY_WEBHOOK_SECRET",
    }
)


def sanitize_settings_payload(body: bytes) -> bytes:
    try:
        payload: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid settings JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Settings payload must be an object")

    unknown = set(payload) - ALLOWED_SETTINGS_KEYS
    if unknown:
        raise ValueError("Unsupported settings key")

    if any(not isinstance(value, str) for value in payload.values()):
        raise ValueError("Settings values must be strings")

    sanitized = {
        key: value.replace("\r", "").replace("\n", "")
        for key, value in payload.items()
    }
    return json.dumps(sanitized, separators=(",", ":")).encode("utf-8")


class SettingsPayloadGuard:
    """ASGI middleware that validates and normalizes POST /api/v1/settings."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/settings"
        ):
            await self.app(scope, receive, send)
            return

        chunks = []
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                await self.app(scope, receive, send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        try:
            sanitized = sanitize_settings_payload(b"".join(chunks))
        except ValueError as exc:
            response = JSONResponse(status_code=400, content={"detail": str(exc)})
            await response(scope, receive, send)
            return

        sent = False

        async def sanitized_receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {
                "type": "http.request",
                "body": sanitized,
                "more_body": False,
            }

        await self.app(scope, sanitized_receive, send)


def install_settings_guard(app) -> None:
    """Install the settings boundary guard exactly once."""
    if any(m.cls is SettingsPayloadGuard for m in getattr(app, "user_middleware", [])):
        return
    app.add_middleware(SettingsPayloadGuard)
