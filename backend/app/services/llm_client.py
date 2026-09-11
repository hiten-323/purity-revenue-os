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

import os
import httpx

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = os.getenv(
    "NVIDIA_MODEL",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
)

CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def _nvidia_key() -> str:
    # NVIDIA Build currently documents NVIDIA_API_KEY. Accept the LiteLLM-style
    # alias too so deployments can use either convention without code changes.
    return os.getenv("NVIDIA_API_KEY", "") or os.getenv("NVIDIA_NIM_API_KEY", "")


def _nvidia_complete(prompt: str, timeout: float) -> str | None:
    key = _nvidia_key()
    if not key or "your_" in key.lower():
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
                    # Nemotron 3.5 Lightning enables reasoning by default. For
                    # the shared synchronous helper, disable thinking so short
                    # operational prompts do not spend the whole timeout budget
                    # generating a reasoning trace before returning content.
                    "chat_template_kwargs": {"enable_thinking": False},
                    "max_tokens": 1024,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"].get("content")
    except Exception:
        return None


def _cerebras_complete(prompt: str, timeout: float) -> str | None:
    key = os.getenv("CEREBRAS_API_KEY", "")
    if not key or "your_" in key.lower():
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
