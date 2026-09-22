"""Startup helpers for Outreach Intelligence V1.

Call `register_outreach_intelligence(fastapi_app, engine)` from main.py after create_all.
Does not enable any outreach kill-switch flags.
"""
from __future__ import annotations
import importlib
import logging

log = logging.getLogger(__name__)


def register_outreach_intelligence(fastapi_app, engine) -> None:
    """Import models, ensure schema, mount dry/admin API router."""
    # Use importlib so we do not shadow the fastapi_app parameter with the
    # top-level `app` package (import app.foo binds local name `app`).
    importlib.import_module("app.services.outreach_intelligence.models")
    from app.services.outreach_intelligence.event_ledger import ensure_outreach_intelligence_schema
    from app.api.outreach_intelligence_router import router as outreach_intelligence_router

    try:
        ensure_outreach_intelligence_schema(engine)
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach intelligence schema ensure failed: %s", exc)

    # Avoid double-registration if main already included the router
    paths = {getattr(r, "path", None) for r in fastapi_app.routes}
    if not any(p and "outreach-intelligence" in str(p) for p in paths):
        fastapi_app.include_router(outreach_intelligence_router, prefix="/api/v1")
        log.info("outreach intelligence router mounted at /api/v1/outreach-intelligence")
