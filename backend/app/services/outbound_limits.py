"""The only source for outbound ceilings.

A restart, a missing environment variable, or a higher value in the
environment cannot raise these. The environment may only lower a ceiling.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "config" / "outbound_limits.json"
_OWNED = (
    "AI_CALL_CYCLE_MAX_DIALS",
    "AI_CALL_MAX_INFLIGHT",
    "WA_MAX_PER_HOUR",
    "WA_DAILY_CAP",
)


def ceilings() -> dict[str, int]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {name: 0 for name in _OWNED}
    out = {}
    for name in _OWNED:
        try:
            value = int(data[name])
        except (KeyError, TypeError, ValueError):
            value = 0
        out[name] = value if value >= 0 else 0
    return out


def ceiling(name: str) -> int:
    return ceilings().get(name, 0)


def effective(name: str) -> int:
    """The owned ceiling, or a lower environment value. Never higher."""
    cap = ceiling(name)
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return cap
    try:
        asked = int(str(raw).strip())
    except ValueError:
        return cap
    return max(0, min(cap, asked))
