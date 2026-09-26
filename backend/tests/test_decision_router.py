"""Offline tests for the free structured decision layer."""
from __future__ import annotations
import json
from app.services import decision_router

def test_safe_decision(monkeypatch):
    monkeypatch.setenv("AI_BY_HJ_DECISION_DRY_RUN", json.dumps({
        "decision": "EXECUTE", "action": "VERIFY", "confidence": 0.97,
        "risk": "LOW", "reason": "Evidence is sufficient.", "evidence": ["verified source"]
    }))
    out = decision_router.decide({"task": "verify"})
    assert out.executable is True
    assert out.source == "dry_run"

def test_mutation_is_blocked(monkeypatch):
    monkeypatch.setenv("AI_BY_HJ_DECISION_DRY_RUN", json.dumps({
        "decision": "EXECUTE", "action": "NONE", "confidence": 0.99,
        "risk": "LOW", "reason": "SEND email now.", "evidence": ["email present"]
    }))
    out = decision_router.decide({"task": "outreach"})
    assert out.executable is False
    assert "mutating" in (out.blocked_reason or "").lower()

def test_high_risk_requires_human(monkeypatch):
    monkeypatch.setenv("AI_BY_HJ_DECISION_DRY_RUN", json.dumps({
        "decision": "EXECUTE", "action": "VERIFY", "confidence": 0.99,
        "risk": "HIGH", "reason": "Sensitive operation.", "evidence": []
    }))
    out = decision_router.decide({"task": "verify"})
    assert out.executable is False
    assert "human" in (out.blocked_reason or "").lower()

def test_invalid_json_fails_closed(monkeypatch):
    monkeypatch.setenv("AI_BY_HJ_DECISION_DRY_RUN", "not json")
    out = decision_router.decide({"task": "anything"})
    assert out.executable is False
    assert out.decision == "ESCALATE"

def test_provider_response(monkeypatch):
    monkeypatch.delenv("AI_BY_HJ_DECISION_DRY_RUN", raising=False)
    monkeypatch.setattr(decision_router, "complete", lambda *a, **k: (
        '{"decision":"EXECUTE","action":"CLASSIFY","confidence":0.95,'
        '"risk":"LOW","reason":"Evidence supports classification.",'
        '"evidence":["category=RETAIL"]}', "nvidia"))
    out = decision_router.decide({"task": "classify"})
    assert out.executable is True
    assert out.source == "nvidia"
    assert out.action == "CLASSIFY"
