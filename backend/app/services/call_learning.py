"""Controlled, non-code-generating learning layer for AI calling.

Learning is advisory: it records evidence from completed calls and produces
strategy hints. It never changes consent/DND/retry/callback policy and never
rewrites production code or prompts automatically.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from app.models.models import AgentLog, WorkflowEvent


TERMINAL_OUTCOMES = frozenset({"OPT_OUT", "WRONG_NUMBER", "NOT_INTERESTED"})


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def record_call_learning(db, lead, *, outcome: str, summary: str = "",
                         transcript: str = "", details: dict | None = None,
                         callback_window: str = "") -> None:
    """Persist one structured learning observation.

    AgentLog is deliberately used instead of adding another schema dependency:
    the existing event ledger remains the source of truth and this record is
    an advisory projection that can be rebuilt.
    """
    payload = {
        "kind": "CALL_LEARNING",
        "version": 1,
        "lead_id": getattr(lead, "id", None),
        "company": getattr(lead, "company", None),
        "segment": getattr(lead, "segment", None) or getattr(lead, "division", None),
        "outcome": _norm(outcome),
        "summary": (summary or "")[:2000],
        "transcript": (transcript or "")[:12000],
        "callback_window": (callback_window or "")[:200],
        "details": details or {},
        "recorded_at": datetime.utcnow().isoformat(),
    }
    db.add(AgentLog(
        agent_name="ai_call_learning",
        action="CALL_LEARNING_RECORDED",
        timestamp=datetime.utcnow(),
        payload=payload,
    ))


def build_strategy_hints(db, *, segment: str | None = None,
                         days: int = 30, limit: int = 500) -> dict[str, Any]:
    """Aggregate observed patterns without making a decision about calling.

    Only repeated evidence is surfaced. Terminal outcomes are reported but
    never converted into a recommendation to retry.
    """
    since = datetime.utcnow() - timedelta(days=max(1, days))
    q = db.query(AgentLog).filter(
        AgentLog.agent_name == "ai_call_learning",
        AgentLog.action == "CALL_LEARNING_RECORDED",
        AgentLog.timestamp >= since,
    ).order_by(AgentLog.timestamp.desc()).limit(max(1, limit))
    rows = q.all()

    outcomes = Counter()
    objections = Counter()
    callback_windows = Counter()
    segments = Counter()

    for row in rows:
        p = row.payload or {}
        if segment and _norm(p.get("segment")) != _norm(segment):
            continue
        outcomes[_norm(p.get("outcome"))] += 1
        segments[_norm(p.get("segment"))] += 1
        d = p.get("details") or {}
        for key in ("objection", "objection_reason", "current_supplier"):
            value = str(d.get(key) or "").strip()
            if value:
                objections[value.lower()[:160]] += 1
        callback = str(p.get("callback_window") or "").strip()
        if callback:
            callback_windows[callback.lower()[:120]] += 1

    total = sum(outcomes.values())
    return {
        "version": 1,
        "generated_at": datetime.utcnow().isoformat(),
        "observations": total,
        "outcomes": outcomes.most_common(20),
        "objections": objections.most_common(20),
        "callback_windows": callback_windows.most_common(20),
        "segments": segments.most_common(20),
        "confidence_threshold": 5,
        "terminal_outcomes_never_retry": sorted(TERMINAL_OUTCOMES),
        "mode": "ADVISORY_ONLY",
    }
