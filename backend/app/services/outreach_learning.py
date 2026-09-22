"""Closed-loop outreach learning.

This module does NOT choose between email and AI calling. The orchestrator
already evaluates both independently and the automatic cycle executes every
eligible channel.

Its job is narrower and safer:
    previous real outcomes -> evidence -> guidance -> next lead

Only observed history is used. Thin evidence is reported but never promoted
into a hard rule. Safety/permission gates remain outside this module.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import B2BLead, LearnedPattern, ObjectionLearning, WorkflowEvent


MIN_EVIDENCE = 5

POSITIVE_CALL_OUTCOMES = {
    "INTERESTED",
    "SEND_DETAILS",
    "SEND_PRICING",
    "SAMPLE_REQUESTED",
    "MEETING_REQUESTED",
    "CALLBACK",
    "PRICE_OBJECTION",
    "EXISTING_SUPPLIER",
}
ANSWERED_CALL_OUTCOMES = POSITIVE_CALL_OUTCOMES | {
    "NOT_INTERESTED",
    "DO_NOT_CONTACT",
    "WRONG_PERSON",
    "GATEKEEPER",
    "CALL_LATER",
}
NEGATIVE_OUTCOMES = {"NOT_INTERESTED", "DO_NOT_CONTACT"}

_EMAIL_INTENT_EVENTS = {
    "EMAIL_REPLY_RECEIVED",
    "REPLY_RECEIVED",
}
_EMAIL_POSITIVE = {
    "CATALOGUE_REQUESTED",
    "SAMPLE_REQUESTED",
    "PRICING_REQUESTED",
    "MEETING_REQUESTED",
    "CALLBACK",
    "INTERESTED",
}


def _category_peers(db: Session, lead: B2BLead, category: str) -> list[B2BLead]:
    """Comparable previously contacted leads, excluding the current lead."""
    from app.services.smart_outreach import OutreachProfile, OutreachTouch

    rows = (
        db.query(B2BLead)
        .join(OutreachProfile, OutreachProfile.lead_id == B2BLead.id)
        .filter(
            OutreachProfile.category == category,
            B2BLead.id != lead.id,
        )
        .all()
    )
    # Only leads with an actual outreach footprint are useful experience.
    contacted = []
    for row in rows:
        has_touch = (
            db.query(OutreachTouch.id)
            .filter(OutreachTouch.lead_id == row.id)
            .first()
            is not None
        )
        has_call = bool(getattr(row, "ai_call_count", 0) or getattr(row, "call_outcome_last", None))
        if has_touch or has_call:
            contacted.append(row)
    return contacted


def _email_evidence(db: Session, lead_ids: list[int]) -> dict[str, Any]:
    from app.services.smart_outreach import OutreachTouch

    if not lead_ids:
        return {"sent": 0, "delivered": 0, "replies": 0, "positive": 0}

    touches = (
        db.query(OutreachTouch)
        .filter(
            OutreachTouch.lead_id.in_(lead_ids),
            OutreachTouch.channel == "email",
        )
        .all()
    )
    proven = [t for t in touches if t.status in ("SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ")]
    delivered = [t for t in touches if t.status in ("DELIVERED", "READ")]
    replies = (
        db.query(WorkflowEvent)
        .filter(
            WorkflowEvent.lead_id.in_(lead_ids),
            WorkflowEvent.event_type.in_(tuple(_EMAIL_INTENT_EVENTS)),
        )
        .all()
    )
    positive = 0
    for event in replies:
        payload = event.payload if isinstance(event.payload, dict) else {}
        intent = str(payload.get("intent") or payload.get("outcome") or "").upper()
        if intent in _EMAIL_POSITIVE:
            positive += 1
        elif not intent:
            # A real human reply is still meaningful evidence even if the
            # older ingestion path did not persist an intent.
            positive += 1
    return {
        "sent": len(proven),
        "delivered": len(delivered),
        "replies": len(replies),
        "positive": positive,
    }


def _call_evidence(peers: list[B2BLead]) -> dict[str, Any]:
    outcomes = [
        str(getattr(p, "call_outcome_last", "") or "").upper()
        for p in peers
        if getattr(p, "call_outcome_last", None)
    ]
    counts = Counter(outcomes)
    answered = sum(1 for x in outcomes if x in ANSWERED_CALL_OUTCOMES)
    positive = sum(1 for x in outcomes if x in POSITIVE_CALL_OUTCOMES)
    return {
        "calls": len(outcomes),
        "answered": answered,
        "positive": positive,
        "outcomes": counts,
    }


def _learned_reply_rate(db: Session, category: str) -> tuple[float | None, int]:
    row = (
        db.query(LearnedPattern)
        .filter(
            LearnedPattern.scope == "category_channel",
            LearnedPattern.key == f"{category}:email",
            LearnedPattern.metric == "reply_rate_pct",
        )
        .first()
    )
    if not row or (row.sample_size or 0) < MIN_EVIDENCE:
        return None, 0
    return float(row.value or 0), int(row.sample_size or 0)


def _top_objection(db: Session, peer_ids: list[int]) -> tuple[str | None, int]:
    if not peer_ids:
        return None, 0
    rows = (
        db.query(ObjectionLearning.objection_type)
        .filter(ObjectionLearning.lead_id.in_(peer_ids))
        .all()
    )
    counts = Counter(str(r[0] or "").strip() for r in rows if str(r[0] or "").strip())
    return (counts.most_common(1)[0] if counts else (None, 0))


def _top_call_outcome(evidence: dict[str, Any]) -> tuple[str | None, int]:
    counts = evidence["outcomes"]
    return counts.most_common(1)[0] if counts else (None, 0)


def _cta_for(intent: str | None) -> str:
    mapping = {
        "SAMPLE_REQUESTED": "sample",
        "CATALOGUE_REQUESTED": "catalogue",
        "PRICING_REQUESTED": "commercial_details",
        "MEETING_REQUESTED": "call",
        "CALLBACK": "call",
        "INTERESTED": "catalogue",
    }
    return mapping.get(intent or "", "catalogue")


def _question_for(intent: str | None, objection: str | None) -> str:
    if objection:
        return f"Previous similar conversations most often raised {objection.lower()} — acknowledge it without inventing terms and offer founder follow-up."
    return {
        "SAMPLE_REQUESTED": "Would a small sample be useful for you to evaluate first?",
        "CATALOGUE_REQUESTED": "Would you like the range and pack sizes by email?",
        "PRICING_REQUESTED": "If commercial details matter, I can have the founder share the current terms.",
        "MEETING_REQUESTED": "Would a short conversation with the founder be useful?",
        "CALLBACK": "What would be a convenient time for the founder to follow up?",
        "INTERESTED": "Would you like the range and a sample option?",
    }.get(intent or "", "Would you be open to hearing how Purity Beans could fit your coffee requirement?")


def build_learning_context(db: Session, lead: B2BLead, profile=None) -> dict[str, Any]:
    """Return evidence-backed guidance for THIS lead, based on earlier leads.

    The result is intentionally advisory: it can shape copy/questions but
    cannot grant permission, suppress an eligible channel, or alter safety.
    """
    if profile is None:
        from app.services.smart_outreach import classify_lead
        profile = classify_lead(db, lead)

    category = (profile.category or "UNKNOWN").upper()
    peers = _category_peers(db, lead, category)
    peer_ids = [p.id for p in peers]
    email = _email_evidence(db, peer_ids)
    calls = _call_evidence(peers)
    reply_rate, reply_sample = _learned_reply_rate(db, category)
    objection, objection_count = _top_objection(db, peer_ids)
    top_outcome, top_outcome_count = _top_call_outcome(calls)

    # Prefer observed positive outcomes. If there is no positive call evidence,
    # use the email evidence. No single thin observation becomes a rule.
    learned_intent = None
    evidence_count = 0
    if top_outcome in POSITIVE_CALL_OUTCOMES and top_outcome_count >= MIN_EVIDENCE:
        learned_intent = top_outcome
        evidence_count = top_outcome_count
    elif email["positive"] >= MIN_EVIDENCE:
        # Infer the CTA from persisted reply intents where available.
        events = (
            db.query(WorkflowEvent)
            .filter(
                WorkflowEvent.lead_id.in_(peer_ids) if peer_ids else WorkflowEvent.id == -1,
                WorkflowEvent.event_type.in_(tuple(_EMAIL_INTENT_EVENTS)),
            )
            .all()
        )
        intents = Counter(
            str((e.payload or {}).get("intent") or (e.payload or {}).get("outcome") or "").upper()
            for e in events
        )
        for intent, count in intents.most_common():
            if intent in _EMAIL_POSITIVE and count >= MIN_EVIDENCE:
                learned_intent, evidence_count = intent, count
                break

    cta = _cta_for(learned_intent)
    question = _question_for(learned_intent, objection)
    summary_parts = []
    if reply_rate is not None:
        summary_parts.append(f"email reply rate {reply_rate:.1f}% over {reply_sample} sends")
    if calls["calls"]:
        summary_parts.append(f"{calls['positive']} positive call outcomes over {calls['calls']} recorded calls")
    if objection:
        summary_parts.append(f"top objection: {objection} ({objection_count})")
    if learned_intent:
        summary_parts.append(f"learned CTA from {evidence_count} comparable outcomes: {learned_intent}")
    summary = "; ".join(summary_parts) if summary_parts else "no comparable evidence yet — use the baseline message"

    return {
        "category": category,
        "comparable_leads": len(peers),
        "email": email,
        "calls": {
            "total": calls["calls"],
            "answered": calls["answered"],
            "positive": calls["positive"],
            "top_outcome": top_outcome,
        },
        "reply_rate_pct": reply_rate,
        "reply_sample": reply_sample,
        "top_objection": objection,
        "top_objection_count": objection_count,
        "learned_intent": learned_intent,
        "evidence_count": evidence_count,
        "recommended_cta": cta,
        "recommended_call_question": question,
        "summary": summary,
    }
