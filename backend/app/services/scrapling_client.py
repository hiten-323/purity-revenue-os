"""Optional Scrapling MCP client for web research/enrichment.

Purity Revenue OS keeps Scrapling outside the application process. The service
connects to a separately managed Scrapling MCP server over Streamable HTTP and
fails closed to the existing HTTP harvesters when Scrapling is unavailable.

Scrapling 0.4.15 declares MCP >=1.27 and its current MCP server remains on the
MCP v1 SDK line, so this integration intentionally pins mcp<2.

No outbound sales action is exposed here: this module only reads web content.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any


DEFAULT_URL = "http://127.0.0.1:8000/mcp"


def enabled() -> bool:
    """Return True only when the feature has been explicitly enabled."""
    value = os.getenv("SCRAPLING_ENABLED", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def endpoint() -> str:
    return os.getenv("SCRAPLING_MCP_URL", DEFAULT_URL).strip() or DEFAULT_URL


def _token() -> str:
    return os.getenv("SCRAPLING_MCP_AUTH_TOKEN", "").strip()


def _run(coro):
    """Run an async MCP operation from the existing synchronous service layer."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Scrapling sync client cannot run inside an active event loop")


def _content_to_dict(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return [_content_to_dict(v) for v in value]
    return value


def _result_payload(result: Any) -> dict:
    """Normalize MCP CallToolResult into a plain response dictionary."""
    structured = getattr(result, "structured_content", None)
    if structured:
        payload = _content_to_dict(structured)
        if isinstance(payload, dict):
            return payload

    chunks: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return {"status": None, "content": "\n".join(chunks)}


async def _call_tool_async(name: str, arguments: dict[str, Any]) -> dict:
    """Call a Scrapling MCP tool using the MCP v1 Streamable HTTP client."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    token = _token()
    kwargs: dict[str, Any] = {
        "timeout": 30,
        "sse_read_timeout": 300,
    }
    if token:
        kwargs["headers"] = {"Authorization": f"Bearer {token}"}

    async with streamable_http_client(endpoint(), **kwargs) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments=arguments)
            if getattr(result, "is_error", False):
                raise RuntimeError(f"Scrapling MCP tool {name} returned an error")
            return _result_payload(result)


def call_tool(name: str, arguments: dict[str, Any]) -> dict:
    """Synchronous bridge used by the existing enrichment workers."""
    if not enabled():
        raise RuntimeError("Scrapling is disabled (set SCRAPLING_ENABLED=1)")
    return _run(_call_tool_async(name, arguments))


def fetch_url(
    url: str,
    *,
    dynamic: bool = False,
    stealth: bool = False,
    css_selector: str | None = None,
    timeout_seconds: float = 30,
) -> dict:
    """Fetch a URL through Scrapling and return its structured response.

    Routing policy:
      - stealth -> stealthy_fetch
      - dynamic -> fetch (Playwright)
      - otherwise -> make_request (fast HTTP)
    """
    if stealth:
        tool = "stealthy_fetch"
        args: dict[str, Any] = {
            "url": url,
            "extraction_type": "html",
            "main_content_only": False,
            "timeout": int(timeout_seconds * 1000),
        }
    elif dynamic:
        tool = "fetch"
        args = {
            "url": url,
            "extraction_type": "html",
            "main_content_only": False,
            "timeout": int(timeout_seconds * 1000),
        }
    else:
        tool = "make_request"
        args = {
            "url": url,
            "method": "GET",
            "extraction_type": "html",
            "main_content_only": False,
            "timeout": timeout_seconds,
            "follow_redirects": "safe",
        }
    if css_selector:
        args["css_selector"] = css_selector
    return call_tool(tool, args)


def content_from_response(response: dict) -> str:
    """Extract Scrapling ResponseModel.content from a normalized result."""
    content = response.get("content", "")
    if isinstance(content, str):
        return content
    return str(content or "")
