from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from typing import Any

from app.services.llm_client import complete

MIN_CONFIDENCE = float(os.getenv("DECISION_ENGINE_MIN_CONFIDENCE", "0.90"))
ALLOWED_DECISIONS = {"EXECUTE", "RETRY", "ESCALATE", "WAIT", "STOP", "NO_ACTION"}
ALLOWED_RISKS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
SAFE_ACTIONS = {"NONE", "INSPECT", "CLASSIFY", "VERIFY", "DRAFT", "RETRY_READ", "ESCALATE_HUMAN", "WAIT", "STOP"}
MUTATING_MARKERS = ("SEND", "CALL", "DELETE", "UPDATE", "CREATE", "PUBLISH", "PAY", "PURCHASE", "DISPATCH", "MESSAGE", "WHATSAPP")

@dataclass(frozen=True)
class DecisionResult:
    decision: str
    action: str
    confidence: float
    risk: str
    reason: str
    evidence: tuple
    source: str
    executable: bool
    blocked_reason: Any = None

    def to_dict(self):
        out = asdict(self)
        out["evidence"] = list(self.evidence)
        return out

def _fallback(source, reason):
    return DecisionResult("ESCALATE", "ESCALATE_HUMAN", 0.0, "HIGH", reason, (), source, False, "No valid structured model decision.")

def _validate(raw, source):
    decision = str(raw.get("decision", "")).upper().strip()
    action = str(raw.get("action", "")).upper().strip()
    risk = str(raw.get("risk", "")).upper().strip()
    reason = str(raw.get("reason", "")).strip()
    try:
        confidence = float(raw.get("confidence", -1))
    except (TypeError, ValueError):
        confidence = -1
    evidence = raw.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
    evidence = tuple(str(x).strip() for x in evidence[:8] if str(x).strip())
    if decision not in ALLOWED_DECISIONS:
        return _fallback(source, "Invalid decision enum.")
    if action not in SAFE_ACTIONS:
        return _fallback(source, "Invalid action enum.")
    if risk not in ALLOWED_RISKS:
        return _fallback(source, "Invalid risk enum.")
    if not 0 <= confidence <= 1:
        return _fallback(source, "Confidence is outside 0..1.")
    if not reason:
        return _fallback(source, "Missing decision reason.")

    blob = (decision + " " + action + " " + reason).upper()
    executable = decision == "EXECUTE" and confidence >= MIN_CONFIDENCE and risk == "LOW"
    blocked = None
    if any(marker in blob for marker in MUTATING_MARKERS):
        executable = False
        blocked = "Potential external/state-mutating action is outside the decision-layer permission boundary."
    elif risk in {"HIGH", "CRITICAL"}:
        executable = False
        blocked = "High/critical risk requires human approval."
    elif decision != "EXECUTE":
        executable = False
        blocked = "Decision is " + decision + ", not EXECUTE."
    elif confidence < MIN_CONFIDENCE:
        executable = False
        blocked = "Confidence is below the decision threshold."
    elif risk != "LOW":
        executable = False
        blocked = "Risk is " + risk + "."
    return DecisionResult(decision, action, confidence, risk, reason, evidence, source, executable, blocked)

def decide(context, timeout=20.0):
    dry_run = os.getenv("AI_BY_HJ_DECISION_DRY_RUN", "").strip()
    if dry_run:
        try:
            return _validate(json.loads(dry_run), "dry_run")
        except (json.JSONDecodeError, TypeError):
            return _fallback("dry_run", "Invalid AI_BY_HJ_DECISION_DRY_RUN JSON.")

    prompt = """You are the AI BY HJ decision layer.
Return ONLY JSON with decision, action, confidence, risk, reason, evidence.
Never invent facts. If evidence is insufficient, choose WAIT or ESCALATE.
You cannot execute tools or communicate externally.
decision: EXECUTE|RETRY|ESCALATE|WAIT|STOP|NO_ACTION
action: NONE|INSPECT|CLASSIFY|VERIFY|DRAFT|RETRY_READ|ESCALATE_HUMAN|WAIT|STOP
confidence: 0..1
risk: LOW|MEDIUM|HIGH|CRITICAL
High/critical risk must use ESCALATE or STOP.
External communication, deletion, payment, publishing, inventory/order mutation,
and CRM mutation are never directly executable by this layer.

EVIDENCE:
""" + json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)

    content, source = complete(prompt, nvidia_timeout=timeout, cerebras_timeout=min(timeout, 15.0), ollama_timeout=min(timeout, 30.0))
    if not content:
        return _fallback(source, "No provider returned a decision.")
    try:
        start = content.find("{")
        end = content.rfind("}")
        raw = json.loads(content[start:end + 1] if start >= 0 and end > start else content)
    except (json.JSONDecodeError, TypeError):
        return _fallback(source, "Provider returned invalid JSON.")
    return _validate(raw, source)

def lead_context(lead, existing_next_action=None):
    return {"domain": "b2b_lead", "facts": {
        "lead_id": getattr(lead, "id", None),
        "company": getattr(lead, "company", None),
        "city": getattr(lead, "city", None),
        "status": getattr(lead, "status", None),
        "division": getattr(lead, "division", None),
        "email_present": bool(getattr(lead, "email", None)),
        "phone_present": bool(getattr(lead, "phone", None)),
        "email_trust": getattr(lead, "email_trust", None),
        "email_confidence": getattr(lead, "email_confidence", None),
        "next_followup_date": getattr(lead, "next_followup_date", None),
        "estimated_value": getattr(lead, "estimated_value", None),
        "existing_next_action": existing_next_action,
    }}
