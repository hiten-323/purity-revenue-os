from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.auth import require_api_admin


def test_admin_auth_requires_configuration(monkeypatch):
    monkeypatch.delenv("API_ADMIN_SECRET", raising=False)
    monkeypatch.delenv("GATEWAY_ADMIN_SECRET", raising=False)
    app = FastAPI()
    def mutate(request: Request):
        require_api_admin(request)
        return {"ok": True}

    app.post("/mutate")(mutate)

    with TestClient(app) as client:
        response = client.post("/mutate")

    assert response.status_code == 503


def test_admin_auth_accepts_configured_secret(monkeypatch):
    monkeypatch.setenv("API_ADMIN_SECRET", "test-admin-secret")
    app = FastAPI()
    def mutate(request: Request):
        require_api_admin(request)
        return {"ok": True}

    app.post("/mutate")(mutate)

    with TestClient(app) as client:
        response = client.post(
            "/mutate", headers={"Authorization": "Bearer test-admin-secret"}
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_shopify_webhook_fails_closed_without_secret(monkeypatch):
    from app.api.endpoints import _verify_shopify_hmac

    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    assert _verify_shopify_hmac(b"{}", "") is False
