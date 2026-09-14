from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.orm import sessionmaker

from app.api.whatsapp_gateway import router as whatsapp_router
from app.database.database import Base, get_db
from app.database.schema import assert_schema_compatible


def test_schema_compatibility_fails_closed_for_missing_column():
    metadata = MetaData()
    Table("required_table", metadata, Column("id", Integer, primary_key=True), Column("name", String))
    engine = create_engine("sqlite://")
    Table("required_table", MetaData(), Column("id", Integer, primary_key=True)).create(engine)

    with pytest.raises(RuntimeError, match="required_table.name"):
        assert_schema_compatible(engine, metadata)


def test_schema_compatibility_accepts_matching_schema():
    metadata = MetaData()
    Table("required_table", metadata, Column("id", Integer, primary_key=True), Column("name", String))
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    assert_schema_compatible(engine, metadata)


def test_health_is_unhealthy_when_required_redis_is_unavailable(monkeypatch):
    import app.main as main

    class _UnavailableRedis:
        def ping(self):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(main.redis, "from_url", lambda *args, **kwargs: _UnavailableRedis())
    db = SimpleNamespace(execute=lambda statement: True)

    response = main.health_check(db)

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "unhealthy",
        "database": "connected",
        "redis": "disconnected",
    }


def _gateway_client(monkeypatch):
    import app.api.whatsapp_gateway as gateway

    calls = []

    class _Client:
        def send(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                provider_accepted=True,
                status="PROVIDER_ACCEPTED",
                reason="accepted",
                message_id="wamid.test",
            )

    monkeypatch.setattr(gateway, "WhatsAppGatewayClient", lambda: _Client())
    return calls


def _gateway_app(db):
    app = FastAPI()
    app.include_router(whatsapp_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    return app


def test_authenticated_gateway_rejects_caller_campaign_without_provider_call(monkeypatch):
    monkeypatch.setenv("KLAVIYO_WEBHOOK_SECRET", "gateway-secret")
    calls = _gateway_client(monkeypatch)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    payload = {
        "profile_id": "profile-1",
        "phone": "919084958495",
        "lifecycle_stage": "ATC",
        "source_event_id": "event-1",
        "campaign_name": "CALLER_SUPPLIED_TEMPLATE",
        "whatsapp_marketing_consent": "SUBSCRIBED",
    }

    try:
        with TestClient(_gateway_app(db)) as client:
            response = client.post(
                "/api/v1/webhooks/klaviyo/whatsapp",
                json=payload,
                headers={"X-Webhook-Secret": "gateway-secret"},
            )
    finally:
        db.close()

    assert response.status_code == 400
    assert response.json()["error"] == "unknown_campaign"
    assert calls == []


def test_gateway_ledger_missing_admin_configuration_is_503(monkeypatch):
    monkeypatch.delenv("GATEWAY_ADMIN_SECRET", raising=False)
    monkeypatch.delenv("KLAVIYO_WEBHOOK_SECRET", raising=False)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    try:
        with TestClient(_gateway_app(db)) as client:
            response = client.get("/api/v1/webhooks/gateway/ledger")
    finally:
        db.close()

    assert response.status_code == 503


def test_gateway_ledger_invalid_admin_credentials_are_401(monkeypatch):
    monkeypatch.setenv("GATEWAY_ADMIN_SECRET", "configured-admin-secret")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    try:
        with TestClient(_gateway_app(db)) as client:
            response = client.get(
                "/api/v1/webhooks/gateway/ledger",
                headers={"X-Gateway-Secret": "wrong-admin-secret"},
            )
    finally:
        db.close()

    assert response.status_code == 401
