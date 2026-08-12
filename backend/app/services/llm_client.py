"""
Shared LLM completion helper: Cerebras first, local Ollama as a fallback.

WHY A FALLBACK, AND WHY THIS MODEL
-----------------------------------
Cerebras (gpt-oss-120b) is the primary path — free tier, fast (purpose-built
inference hardware), and far stronger than anything that fits on this laptop.
It stays first for every call.

Ollama only fires if Cerebras errors out (key missing, network down, rate
limited) so the app degrades instead of losing contact-attribution or tender
analysis entirely. Benchmarked on this machine (i5-13420H, no GPU, ~3GB free
RAM): qwen3:8b took 75s for a trivial prompt — unusable, it would blow every
timeout in the callers. llama3.2:3b answered the same prompt correctly in
13s, so that's the fallback model. This is deliberately NOT "the best local
model" — it's the fastest one that still returns usable JSON before callers
time out.
"""
from __future__ import annotations

import os
import httpx

CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL = "gpt-oss-120b"

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.2:3b"


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
            ).json()
        return resp["choices"][0]["message"]["content"]
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
            ).json()
        return resp.get("message", {}).get("content")
    except Exception:
        return None


def complete(prompt: str, cerebras_timeout: float = 15.0, ollama_timeout: float = 45.0) -> tuple[str | None, str]:
    """
    Returns (content, source). source is "cerebras", "ollama", or "none" —
    callers that tag results (e.g. analysis["model"]) can use it directly
    instead of hardcoding which engine answered.
    """
    content = _cerebras_complete(prompt, cerebras_timeout)
    if content:
        return content, "cerebras"
    content = _ollama_complete(prompt, ollama_timeout)
    if content:
        return content, "ollama"
    return None, "none"
