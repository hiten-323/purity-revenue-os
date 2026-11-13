"""Unit tests for the Local Read-Only Reporting Bridge (report_bridge.py)."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.error
import pytest
from http.server import ThreadingHTTPServer

from report_bridge import ReadOnlyBridgeHandler, _get_token


@pytest.fixture(scope="module")
def bridge_server():
    """Start the local bridge on a dynamic test port."""
    server = ThreadingHTTPServer(("127.0.0.1", 8019), ReadOnlyBridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield "http://127.0.0.1:8019"
    server.shutdown()
    server.server_close()


def test_bridge_health_endpoint(bridge_server):
    url = f"{bridge_server}/outreach/health"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=35) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["bridge_status"] == "healthy"
        assert data["read_only"] is True
        assert data["production_api_reachable"] is True
        assert data["production_auth_verified"] is True
        assert data["bridge_host"] == "127.0.0.1"


def test_bridge_1hour_status(bridge_server):
    url = f"{bridge_server}/outreach/status?hours=1"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=35) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "success"
        assert data["window"]["hours"] == 1
        assert "overview" in data
        assert "calls" in data
        assert "messages" in data
        assert "system_status" in data


def test_bridge_3hour_status(bridge_server):
    url = f"{bridge_server}/outreach/status?hours=3"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=35) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "success"
        assert data["window"]["hours"] == 3
        assert "activity_summary" in data["overview"]


def test_bridge_token_never_exposed(bridge_server):
    token = _get_token()
    assert token, "Token must be configured for test"

    # Test on /outreach/health
    req_health = urllib.request.Request(f"{bridge_server}/outreach/health")
    with urllib.request.urlopen(req_health, timeout=35) as resp:
        body_text = resp.read().decode("utf-8")
        headers_str = str(resp.headers)
        assert token not in body_text, "Token exposed in health body!"
        assert token not in headers_str, "Token exposed in health headers!"

    # Test on /outreach/status
    req_status = urllib.request.Request(f"{bridge_server}/outreach/status?hours=1")
    with urllib.request.urlopen(req_status, timeout=35) as resp:
        body_text = resp.read().decode("utf-8")
        headers_str = str(resp.headers)
        assert token not in body_text, "Token exposed in status body!"
        assert token not in headers_str, "Token exposed in status headers!"


def test_bridge_rejects_mutating_methods(bridge_server):
    for method in ["POST", "PUT", "PATCH", "DELETE"]:
        url = f"{bridge_server}/outreach/status"
        req = urllib.request.Request(url, data=b"{}", method=method)
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=35)
        assert exc_info.value.code == 405
        err_body = json.loads(exc_info.value.read().decode("utf-8"))
        assert err_body.get("error") == "Method Not Allowed"
        assert err_body.get("read_only") is True
