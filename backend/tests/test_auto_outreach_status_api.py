from __future__ import annotations


def test_read_only_auto_outreach_status_route_is_mounted_and_protected():
    from app.main import SENSITIVE_GET_PATHS, app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/outreach/status" in paths
    assert "/api/v1/outreach/status" in SENSITIVE_GET_PATHS


def test_auto_outreach_status_route_is_get_only():
    from app.main import app

    routes = [route for route in app.routes if getattr(route, "path", None) == "/api/v1/outreach/status"]
    assert routes
    assert routes[0].methods == {"GET"}
