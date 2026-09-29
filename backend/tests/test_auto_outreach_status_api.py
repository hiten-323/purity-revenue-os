from __future__ import annotations

# FastAPI >= 0.13x wraps included routers (``_IncludedRouter``), so
# ``app.routes`` no longer lists their paths flat. The OpenAPI schema is the
# stable public view of what is mounted and with which methods.


def _paths():
    from app.main import app
    return app.openapi()["paths"]


def test_read_only_auto_outreach_status_route_is_mounted_and_protected():
    from app.main import SENSITIVE_GET_PATHS

    assert "/api/v1/outreach/status" in _paths()
    assert "/api/v1/outreach/status" in SENSITIVE_GET_PATHS


def test_auto_outreach_status_route_is_get_only():
    methods = set(_paths()["/api/v1/outreach/status"].keys())
    assert methods == {"get"}
