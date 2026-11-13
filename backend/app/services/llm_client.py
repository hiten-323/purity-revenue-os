"""
Shared LLM completion helper.

Provider order is intentionally fail-safe:
  1. NVIDIA NIM (build.nvidia.com) -- primary. A list of models is tried in
     order (NVIDIA_MODELS, comma-separated; NVIDIA_MODEL is still honoured as
     the first entry). The hosted endpoint queues requests under load, so each
     attempt has a bounded timeout (NVIDIA_TIMEOUT_S, default 25s), the whole
     NVIDIA phase has a budget (NVIDIA_TOTAL_BUDGET_S, default 60s), and a
     model that times out or returns 429/5xx is skipped for
     NVIDIA_MODEL_COOLDOWN_S (default 300s) instead of costing every call.
  2. Cerebras -- kept as fallback config, but only when CEREBRAS_ENABLED is
     not 0; an HTTP 402 (out of credit) disables it in-process for an hour.
  3. Local Ollama fallback.

Reasoning models (gpt-oss, nemotron) spend completion tokens thinking, so the
token budget is NVIDIA_MAX_TOKENS (default 2048) and gpt-oss gets
reasoning_effort=NVIDIA_REASONING_EFFORT (default "low"); nemotron gets
enable_thinking=false. A reasoning-only (empty content) answer is a failure.

The helper remains provider-neutral so callers do not need to know which
model answered. No outbound communication is performed here; this module is
only for analysis/generation used by higher-level services. Settings are read
at call time (backend/.env is loaded by other modules after import).
"""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger(__name__)

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_NVIDIA_MODELS = (
    "google/gemma-4-31b-it",
    "openai/gpt-oss-20b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
)
# Back-compat: the first default model (callers/tests may read this).
NVIDIA_MODEL = DEFAULT_NVIDIA_MODELS[0]

CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

_LOCK = threading.Lock()
_COOLDOWN_UNTIL: dict[str, float] = {}   # provider/model -> monotonic time
_LAST_MODEL = threading.local()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _nvidia_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "") or os.getenv("NVIDIA_NIM_API_KEY", "")


def nvidia_models() -> list[str]:
    raw = os.getenv("NVIDIA_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else list(DEFAULT_NVIDIA_MODELS)
    first = (os.getenv("NVIDIA_MODEL", "") or "").strip()
    if first and not raw:
        models = [first] + [m for m in models if m != first]
    return models


def last_model() -> str | None:
    """The model that produced the most recent completion on this thread."""
    return getattr(_LAST_MODEL, "value", None)


def _cooling(key: str) -> bool:
    with _LOCK:
        return _COOLDOWN_UNTIL.get(key, 0.0) > time.monotonic()


def _cool(key: str, seconds: float) -> None:
    with _LOCK:
        _COOLDOWN_UNTIL[key] = time.monotonic() + max(0.0, seconds)


def _nvidia_body(model: str, prompt: str) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(_env_float("NVIDIA_MAX_TOKENS", 2048)),
        "stream": False,
    }
    m = model.lower()
    if "gpt-oss" in m:
        effort = (os.getenv("NVIDIA_REASONING_EFFORT", "low") or "").strip()
        if effort:
            body["reasoning_effort"] = effort
    elif "nemotron" in m:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


def _nvidia_one(model: str, prompt: str, key: str, timeout: float) -> str | None:
    cooldown = _env_float("NVIDIA_MODEL_COOLDOWN_S", 300)
    try:
        with httpx.Client(timeout=timeout) as c:
            resp = c.post(
                NVIDIA_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=_nvidia_body(model, prompt),
            )
            resp.raise_for_status()
            data = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
        if not content.strip():
            if message.get("reasoning_content"):
                logger.warning("NVIDIA %s returned reasoning-only content; treating as unsuccessful", model)
            else:
                logger.error("NVIDIA %s returned an empty completion", model)
            return None
        return content
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.error("NVIDIA %s request failed: HTTP %s: %s", model, status, exc.response.text[:300])
        if status in (404, 410):
            _cool(f"nvidia:{model}", 3600)      # retired / not on this account
        elif status == 429 or status >= 500:
            _cool(f"nvidia:{model}", cooldown)
        return None
    except httpx.TimeoutException:
        logger.error("NVIDIA %s request timed out after %.1fs (skipping it for %ds)", model, timeout, cooldown)
        _cool(f"nvidia:{model}", cooldown)
        return None
    except Exception:
        logger.exception("NVIDIA %s request failed unexpectedly", model)
        return None


def _nvidia_complete(prompt: str, timeout: float | None) -> str | None:
    key = _nvidia_key()
    if not key or "your_" in key.lower():
        logger.warning("NVIDIA provider skipped: NVIDIA_API_KEY is not configured")
        return None
    per_try = timeout if timeout is not None else _env_float("NVIDIA_TIMEOUT_S", 25)
    budget = _env_float("NVIDIA_TOTAL_BUDGET_S", 60)
    started = time.monotonic()
    models = nvidia_models()
    candidates = [m for m in models if not _cooling(f"nvidia:{m}")] or models[:1]
    for model in candidates:
        left = budget - (time.monotonic() - started)
        if left < 3:
            logger.warning("NVIDIA budget %.0fs spent; falling back", budget)
            break
        content = _nvidia_one(model, prompt, key, min(per_try, left))
        if content:
            _LAST_MODEL.value = model
            return content
    return None


def _cerebras_enabled() -> bool:
    return (os.getenv("CEREBRAS_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def _cerebras_complete(prompt: str, timeout: float) -> str | None:
    if not _cerebras_enabled() or _cooling("cerebras"):
        return None
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
        _LAST_MODEL.value = CEREBRAS_MODEL
        return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.error("Cerebras request failed: HTTP %s", status)
        if status == 402:
            logger.error("Cerebras is out of credit (402); disabled for 1h in this process")
            _cool("cerebras", 3600)
        return None
    except Exception:
        logger.exception("Cerebras request failed")
        return None


def _ollama_complete(prompt: str, timeout: float) -> str | None:
    if _cooling("ollama"):
        return None
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
        _LAST_MODEL.value = OLLAMA_MODEL
        return data.get("message", {}).get("content")
    except httpx.ConnectError:
        logger.warning("Ollama not reachable at %s; skipping it for 10 min", OLLAMA_URL)
        _cool("ollama", 600)
        return None
    except Exception:
        logger.exception("Ollama request failed")
        return None


def complete(
    prompt: str,
    nvidia_timeout: float | None = None,
    cerebras_timeout: float = 15.0,
    ollama_timeout: float = 45.0,
) -> tuple[str | None, str]:
    """
    Returns (content, source). source is "nvidia", "cerebras", "ollama", or
    "none". Existing callers can continue using the source tag for provenance;
    last_model() names the exact model.
    """
    _LAST_MODEL.value = None
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
