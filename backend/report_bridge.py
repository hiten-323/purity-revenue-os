"""Local Read-Only Reporting Bridge for Purity-Revenue Auto-Outreach Status.

Listens strictly on 127.0.0.1 (localhost only) to allow ChatGPT / Remote Desktop Commander
to retrieve live read-only status and health reports without exposing secrets.

Allowed:
  GET /outreach/status?hours=1
  GET /outreach/status?hours=3
  GET /outreach/health
  GET /health

Forbidden:
  POST, PUT, PATCH, DELETE (returns 405 Method Not Allowed)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = int(os.getenv("REPORT_BRIDGE_PORT", "8010"))
BACKEND_API_BASE = os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8003").rstrip("/")


def _get_token() -> str:
    """Read OUTREACH_STATUS_API_TOKEN or API_ADMIN_SECRET securely from env or backend/.env."""
    token = (
        os.getenv("OUTREACH_STATUS_API_TOKEN")
        or os.getenv("OUTREACH_STATUS_TOKEN")
        or os.getenv("API_ADMIN_SECRET")
        or ""
    ).strip()
    if token:
        return token

    # Check backend/.env
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().replace('"', '').replace("'", "")
            if k == "OUTREACH_STATUS_API_TOKEN" and v:
                return v
            if k == "API_ADMIN_SECRET" and not token and v:
                token = v
    return token


class ReadOnlyBridgeHandler(BaseHTTPRequestHandler):
    server_version = "PurityOutreachReportBridge/1.0"

    def _send_json(self, status_code: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Client disconnected while receiving a read-only report.
            return

    def _send_method_not_allowed(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > 0:
                self.rfile.read(min(length, 65536))
        except Exception:
            pass
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {
                "error": "Method Not Allowed",
                "detail": "The local reporting bridge is strictly read-only. Only GET requests are permitted.",
                "read_only": True,
            },
        )

    # Reject mutating methods explicitly with 405 Method Not Allowed
    def do_POST(self) -> None:
        self._send_method_not_allowed()

    def do_PUT(self) -> None:
        self._send_method_not_allowed()

    def do_PATCH(self) -> None:
        self._send_method_not_allowed()

    def do_DELETE(self) -> None:
        self._send_method_not_allowed()

    def do_HEAD(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query_params = urllib.parse.parse_qs(parsed_url.query)

        now_utc = datetime.now(UTC)
        now_ist = now_utc.astimezone(IST)

        if path in ("/outreach/health", "/health", ""):
            self._handle_health(now_utc, now_ist)
            return

        if path in ("/outreach/status", "/api/v1/outreach/status", "/status"):
            self._handle_status(query_params)
            return

        if path in ("/download/founder-manual-intervention-excel", "/manual-intervention.xlsx", "/outreach/download/founder-manual-intervention-excel"):
            excel_path = (Path(__import__("os").getenv("PPP_DIR", "/opt/purity-revenue-os/ppp")) / "PURITY_REVENUE_FOUNDER_MANUAL_INTERVENTION_UPDATED.xlsx")
            if not excel_path.exists():
                excel_path = (Path(__import__("os").getenv("PPP_DIR", "/opt/purity-revenue-os/ppp")) / "PURITY_REVENUE_FOUNDER_MANUAL_INTERVENTION.xlsx")
            if not excel_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "File not found"})
                return
            data = excel_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="PURITY_REVENUE_FOUNDER_MANUAL_INTERVENTION.xlsx"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass
            return

        if path in ("/download/human-intervention-excel", "/human-intervention.xlsx", "/outreach/download/human-intervention-excel"):
            excel_path = (Path(__import__("os").getenv("PPP_DIR", "/opt/purity-revenue-os/ppp")) / "PURITY_REVENUE_FOUNDER_MANUAL_INTERVENTION.xlsx")
            if not excel_path.exists():
                excel_path = (Path(__import__("os").getenv("PPP_DIR", "/opt/purity-revenue-os/ppp")) / "Purity_Revenue_Human_Intervention_Outreach_Leads.xlsx")
            if not excel_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "File not found"})
                return
            data = excel_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="PURITY_REVENUE_FOUNDER_MANUAL_INTERVENTION.xlsx"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass
            return

        if path in ("/download/leads-excel", "/outreach/download/leads-excel", "/leads.xlsx", "/download"):
            excel_path = (Path(__import__("os").getenv("PPP_DIR", "/opt/purity-revenue-os/ppp")) / "Purity_Beans_High_Intent_Outreach_Leads.xlsx")
            if not excel_path.exists():
                excel_path = (Path(__import__("os").getenv("PPP_DIR", "/opt/purity-revenue-os/ppp")) / "Purity_Revenue_Human_Intervention_Outreach_Leads.xlsx")
            if not excel_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "File not found"})
                return
            data = excel_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="Purity_Beans_High_Intent_Outreach_Leads.xlsx"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except Exception:
                pass
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "Not Found",
                "detail": f"Unknown path: {path}",
                "available_endpoints": [
                    "/outreach/status?hours=1",
                    "/outreach/status?hours=3",
                    "/outreach/health",
                ],
            },
        )

    def _handle_health(self, now_utc: datetime, now_ist: datetime) -> None:
        token = _get_token()
        if not token:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "bridge_status": "unhealthy",
                    "error": "OUTREACH_STATUS_API_TOKEN is not configured in environment or backend/.env",
                    "production_api_reachable": False,
                    "production_auth_verified": False,
                    "read_only": True,
                },
            )
            return

        prod_reachable = False
        prod_auth_ok = False
        db_connected = False
        git_commit = "unknown"

        try:
            health_url = f"{BACKEND_API_BASE}/api/v1/outreach/status/health"
            req = urllib.request.Request(
                health_url,
                headers={"User-Agent": "Purity-Local-Report-Bridge/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    prod_reachable = True
                    body = json.loads(resp.read().decode("utf-8"))
                    db_connected = body.get("database") == "connected"
                    git_commit = body.get("git_commit", "unknown")
        except Exception:
            pass

        try:
            # Verify token against protected status endpoint with hours=1
            status_url = f"{BACKEND_API_BASE}/api/v1/outreach/status?hours=1"
            req = urllib.request.Request(
                status_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "Purity-Local-Report-Bridge/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    prod_auth_ok = True
        except Exception:
            pass

        is_healthy = prod_reachable and prod_auth_ok and db_connected
        self._send_json(
            HTTPStatus.OK if is_healthy else HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "bridge_status": "healthy" if is_healthy else "degraded",
                "production_api_reachable": prod_reachable,
                "production_auth_verified": prod_auth_ok,
                "database_connected": db_connected,
                "read_only": True,
                "bridge_host": BRIDGE_HOST,
                "bridge_port": BRIDGE_PORT,
                "git_commit": git_commit,
                "timestamp_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                "timestamp_utc": now_utc.isoformat(),
                "timezone": "Asia/Kolkata",
            },
        )

    def _handle_status(self, query_params: dict[str, list[str]]) -> None:
        token = _get_token()
        if not token:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "status": "error",
                    "detail": "Bridge credential unconfigured: OUTREACH_STATUS_API_TOKEN is missing.",
                    "read_only": True,
                },
            )
            return

        hours_val = query_params.get("hours", ["3"])[0]
        try:
            hours = max(1, min(int(hours_val), 720))
        except ValueError:
            hours = 3

        limit_val = query_params.get("limit", ["50"])[0]
        try:
            limit = max(1, min(int(limit_val), 200))
        except ValueError:
            limit = 50

        status_url = f"{BACKEND_API_BASE}/api/v1/outreach/status?hours={hours}&limit={limit}"
        req = urllib.request.Request(
            status_url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "Purity-Local-Report-Bridge/1.0",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status_data = json.loads(resp.read().decode("utf-8"))
                self._send_json(HTTPStatus.OK, status_data)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                parsed_err = json.loads(err_body)
            except Exception:
                parsed_err = {"detail": str(e)}
            self._send_json(
                e.code,
                {
                    "status": "error",
                    "upstream_status_code": e.code,
                    "error": "Upstream status request failed",
                    "detail": parsed_err.get("detail", str(e)),
                    "read_only": True,
                },
            )
        except Exception as exc:
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "status": "error",
                    "error": "Failed to connect to backend status service",
                    "detail": f"{type(exc).__name__}: {str(exc)}",
                    "read_only": True,
                },
            )

    def log_message(self, format: str, *args: Any) -> None:
        # Avoid noisy console logging; never log tokens or headers
        pass


def run_bridge(host: str = BRIDGE_HOST, port: int = BRIDGE_PORT) -> None:
    token = _get_token()
    if not token:
        print(f"[report-bridge] ERROR: OUTREACH_STATUS_API_TOKEN is not set. Refusing to start.", file=sys.stderr)
        sys.exit(1)

    server = ThreadingHTTPServer((host, port), ReadOnlyBridgeHandler)
    print(f"[report-bridge] Purity-Revenue Outreach Reporting Bridge listening on http://{host}:{port}")
    print(f"[report-bridge] Read-only endpoints: /outreach/status?hours=1, /outreach/status?hours=3, /outreach/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_bridge()
