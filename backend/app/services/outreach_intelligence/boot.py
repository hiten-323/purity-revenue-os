"""Startup helpers for Outreach Intelligence V1.

Call `register_outreach_intelligence(app, engine)` from main.py after create_all.
Does not enable any outreach kill-switch flags.
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def register_outreach_intelligence(app, engine) -> None:
    """Import models, ensure schema, mount dry/admin API router."""
    import app.services.outreach_intelligence.models  # noqa: F401
    from app.services.outreach_intelligence.event_ledger import ensure_outreach_intelligence_schema
    from app.api.outreach_intelligence_router import router as outreach_intelligence_router

    try:
        ensure_outreach_intelligence_schema(engine)
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach intelligence schema ensure failed: %s", exc)

    # Avoid double-registration if main already included the router
    paths = {getattr(r, "path", None) for r in app.routes}
    if not any(p and "outreach-intelligence" in str(p) for p in paths):
        app.include_router(outreach_intelligence_router, prefix="/api/v1")
        log.info("outreach intelligence router mounted at /api/v1/outreach-intelligence")
