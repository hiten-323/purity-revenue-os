"""
Shared LLM completion helper.

Provider order is intentionally fail-safe:
  1. NVIDIA NIM / hosted Nemotron when NVIDIA_API_KEY is configured
  2. Existing Cerebras path
  3. Local Ollama fallback

The helper remains provider-neutral so callers do not need to know which
model answered. No outbound communication is performed here; this module is
only for analysis/generation used by higher-level services.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")

CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def _nvidia_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "") or os.getenv("NVIDIA_NIM_API_KEY", "")


def _nvidia_complete(prompt: str, timeout: float) -> str | None:
    key = _nvidia_key()
    if not key or "your_" in key.lower():
        logger.warning("NVIDIA provider skipped: NVIDIA_API_KEY is not configured")
        return None
    try:
        with httpx.Client(timeout=timeout) as c:
            resp = c.post(
                NVIDIA_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": NVIDIA_MODEL,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                    "chat_template_kwargs": {"enable_thinking": False},
                    "max_tokens": 1024,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        if not content and message.get("reasoning_content"):
            logger.warning("NVIDIA returned reasoning-only content; treating as unsuccessful")
            return None
        if not content:
            logger.error("NVIDIA returned an empty completion")
            return None
        return content
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = exc.response.text[:500]
        logger.error("NVIDIA request failed: HTTP %s: %s", status, detail)
        return None
    except httpx.TimeoutException:
        logger.error("NVIDIA request timed out after %.1fs", timeout)
        return None
    except Exception:
        logger.exception("NVIDIA request failed unexpectedly")
        return None


def _cerebras_complete(prompt: str, timeout: float) -> str | None:
    key = os.getenv("CEREBRAS_API_KEY", "")
    if not key or "your_" in key.lower():
        logger.warning("Cerebras provider skipped: CEREBRAS_API_KEY is not configured")
        return None
    try:
        with httpx.Client(timeout=timeout) as c:
            resp = c.post(
                CEREBRAS_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": CEREBRAS_MODEL,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("Cerebras request failed")
        return None


def _ollama_complete(prompt: str, timeout: float) -> str | None:
    try:
        with httpx.Client(timeout=timeout) as c:
            resp = c.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "stream": False,
                    "options": {"temperature": 0},
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("message", {}).get("content")
    except Exception:
        logger.exception("Ollama request failed")
        return None


def complete(
    prompt: str,
    nvidia_timeout: float = 60.0,
    cerebras_timeout: float = 15.0,
    ollama_timeout: float = 45.0,
) -> tuple[str | None, str]:
    """
    Returns (content, source). source is "nvidia", "cerebras", "ollama", or
    "none". Existing callers can continue using the source tag for provenance.
    """
    content = _nvidia_complete(prompt, nvidia_timeout)
    if content:
        return content, "nvidia"

    content = _cerebras_complete(prompt, cerebras_timeout)
    if content:
        return content, "cerebras"

    content = _ollama_complete(prompt, ollama_timeout)
    if content:
        return content, "ollama"

    return None, "none"
