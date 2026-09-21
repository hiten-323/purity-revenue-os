"""Closed-loop learning for email + AI-call outreach.

This module is observation/learning only. It never grants permission to contact a
lead and never overrides DND, opt-out, cooldown, provider or calling gates.

It turns the existing WorkflowEvent, EmailDraft, LeadInteraction and
LearnedPattern stores into a single evidence loop:
attempt -> delivery/connection -> conversation -> commercial outcome.

Thin evidence is retained but never used for automatic adaptation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from math import sqrt
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import B2BLead, EmailDraft, LeadInteraction, LearnedPattern, WorkflowEvent


EMAIL_SENT = {"EMAIL_SENT"}
EMAIL_DELIVERED = {"EMAIL_DELIVERED"}
EMAIL_BOUNCED = {"EMAIL_BOUNCED", "HARD_BOUNCE"}
EMAIL_REPLIES = {"EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "EMAIL_REPLIED"}
CALL_ATTEMPTS = {"AI_CALL_ATTEMPTED"}
CALL_POSITIVE = {
    "AI_INTEREST_DETECTED", "FOUNDER_CALL_REQUESTED",
    "FOUNDER_CALL_COMPLETED", "COMMERCIAL_OPPORTUNITY",
}
CALL_NEGATIVE = {"AI_NOT_INTERESTED", "AI_WRONG_NUMBER", "AI_OPTED_OUT", "CLOSED_NO_FIT"}
POSITIVE_INTENTS = {
    "CATALOGUE_REQUESTED", "PRICING_REQUESTED", "SAMPLE_REQUESTED",
    "MEETING_REQUESTED", "CALLBACK", "INTERESTED", "NEGOTIATION",
}
MIN_ADAPTATION_SAMPLE = 20


def _category(lead: B2BLead) -> str:
    return (getattr(lead, "division", None) or getattr(lead, "industry", None) or "UNKNOWN").strip().lower()


def _events(db: Session, lead_ids: list[int] | None = None, since_days: int = 90) -> list[WorkflowEvent]:
    q = db.query(WorkflowEvent).filter(
        WorkflowEvent.occurred_at >= datetime.utcnow() - timedelta(days=since_days)
    )
    if lead_ids:
        q = q.filter(WorkflowEvent.lead_id.in_(lead_ids))
    return q.order_by(WorkflowEvent.occurred_at.asc()).all()


def _event_payload(event: WorkflowEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _call_outcome_events(events: list[WorkflowEvent]) -> list[WorkflowEvent]:
    return [
        e for e in events
        if e.event_type == "AI_CALL_DETAILS"
        and str(_event_payload(e).get("outcome") or "").upper()
    ]


def _rate(successes: int, trials: int) -> float:
    return round((successes / trials) * 100.0, 2) if trials else 0.0


def _confidence(trials: int) -> float:
    """Conservative evidence score: 0 until minimum sample, then asymptotic."""
    if trials < MIN_ADAPTATION_SAMPLE:
        return 0.0
    return round(min(1.0, sqrt(trials / (trials + 100.0))), 3)


def rebuild_learning(db: Session, *, since_days: int = 90, min_sample: int = 5) -> dict[str, Any]:
    """Recompute channel/segment metrics from observed events.

    Existing rows are updated, not deleted. The database remains an auditable
    record of the latest measured truth while WorkflowEvent remains the raw
    source of truth.
    """
    leads = db.query(B2BLead).all()
    lead_map = {l.id: l for l in leads}
    events = _events(db, list(lead_map), since_days=since_days)

    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in events:
        lead = lead_map.get(e.lead_id)
        if not lead:
            continue
        category = _category(lead)
        channel = (e.channel or "").lower()

        if e.event_type in EMAIL_SENT and channel == "email":
            grouped[(category, "email")]["attempts"] += 1
        if e.event_type in EMAIL_DELIVERED and channel == "email":
            grouped[(category, "email")]["delivered"] += 1
        if e.event_type in EMAIL_BOUNCED and channel == "email":
            grouped[(category, "email")]["bounced"] += 1
        if e.event_type in EMAIL_REPLIES and channel == "email":
            grouped[(category, "email")]["replies"] += 1

        if e.event_type == "FOUNDER_CALL_PIPELINE" and e.after_status == "AI_CALL_ATTEMPTED":
            grouped[(category, "ai_call")]["attempts"] += 1
        if e.event_type == "FOUNDER_CALL_PIPELINE" and e.after_status == "AI_NO_ANSWER":
            grouped[(category, "ai_call")]["no_answer"] += 1
        if e.event_type == "FOUNDER_CALL_PIPELINE" and e.after_status == "AI_INTEREST_DETECTED":
            # A stage transition proves a meaningful conversation occurred.
            # Commercial positivity is taken from the richer AI_CALL_DETAILS
            # outcome below, so one call cannot be counted twice.
            grouped[(category, "ai_call")]["conversations"] += 1
        if e.event_type == "FOUNDER_CALL_PIPELINE" and e.after_status in CALL_NEGATIVE:
            grouped[(category, "ai_call")]["negative"] += 1
        if e.event_type == "AI_CALL_DETAILS":
            outcome = str(_event_payload(e).get("outcome") or "").upper()
            grouped[(category, "ai_call")]["completed"] += 1
            if outcome in {"INTERESTED", "MEETING_REQUESTED", "CALLBACK_REQUESTED", "HUMAN_HANDOFF", "WHATSAPP_OPT_IN", "SEND_INFO_EMAIL"}:
                grouped[(category, "ai_call")]["positive"] += 1
                grouped[(category, "ai_call")]["conversations"] += 1
            if outcome in {"SAMPLE_REQUESTED", "CATALOGUE_REQUESTED"}:
                grouped[(category, "ai_call")]["next_step"] += 1

    # EmailDraft is the best available delivery evidence for messages already
    # reconciled by the mail layer. Provider-accepted is kept distinct from
    # actual DELIVERED so the learning engine never calls acceptance delivery.
    for draft in db.query(EmailDraft).filter(
        EmailDraft.sent_at.isnot(None),
        EmailDraft.created_at >= datetime.utcnow() - timedelta(days=since_days),
    ).all():
        lead = lead_map.get(draft.lead_id)
        if not lead:
            continue
        g = grouped[(_category(lead), "email")]
        if draft.delivery_status == "DELIVERED":
            g["delivered"] += 1
        if draft.delivery_status == "BOUNCED":
            g["bounced"] += 1
        if draft.reply_status:
            g["replies"] += 1

    metrics = []
    for (category, channel), g in grouped.items():
        attempts = g["attempts"]
        if attempts < min_sample:
            continue

        if channel == "email":
            definitions = {
                "delivery_rate_pct": (g["delivered"], attempts),
                "reply_rate_pct": (g["replies"], attempts),
                "bounce_rate_pct": (g["bounced"], attempts),
            }
        else:
            definitions = {
                "answer_rate_pct": (attempts - g["no_answer"], attempts),
                "conversation_rate_pct": (g["conversations"], attempts),
                "positive_rate_pct": (g["positive"], attempts),
                "next_step_rate_pct": (g["next_step"], attempts),
            }

        for metric, (wins, trials) in definitions.items():
            row = (
                db.query(LearnedPattern)
                .filter(
                    LearnedPattern.scope == "outreach_learning",
                    LearnedPattern.key == f"{category}:{channel}",
                    LearnedPattern.metric == metric,
                )
                .first()
            )
            if row is None:
                row = LearnedPattern(
                    scope="outreach_learning",
                    key=f"{category}:{channel}",
                    metric=metric,
                )
                db.add(row)
            row.value = _rate(wins, trials)
            row.sample_size = trials
            row.wins = wins
            row.updated_at = datetime.utcnow()
            metrics.append({
                "category": category,
                "channel": channel,
                "metric": metric,
                "value": row.value,
                "sample_size": trials,
                "confidence": _confidence(trials),
            })

    db.flush()
    return {"patterns_updated": len(metrics), "metrics": metrics}


def recommendations_for_lead(db: Session, lead: B2BLead) -> dict[str, Any]:
    """Return evidence-backed adaptations without changing channel eligibility.

    Both independently eligible channels remain required. This only determines
    ordering/context and reports evidence; it can never suppress a channel.
    """
    category = _category(lead)
    rows = db.query(LearnedPattern).filter(
        LearnedPattern.scope == "outreach_learning",
        LearnedPattern.key.in_([f"{category}:email", f"{category}:ai_call"]),
    ).all()

    by = defaultdict(dict)
    for r in rows:
        by[r.key][r.metric] = {
            "value": float(r.value or 0),
            "sample_size": int(r.sample_size or 0),
            "confidence": _confidence(int(r.sample_size or 0)),
        }

    email = by.get(f"{category}:email", {})
    call = by.get(f"{category}:ai_call", {})

    call_score = call.get("positive_rate_pct", call.get("conversation_rate_pct", {"value": 0}))["value"]
    email_score = email.get("reply_rate_pct", {"value": 0})["value"]

    # Adapt only when evidence is sufficiently mature. Otherwise preserve the
    # deterministic call-then-email order.
    order = ["call", "email"]
    mature = (
        call.get("positive_rate_pct", {}).get("sample_size", 0) >= MIN_ADAPTATION_SAMPLE
        or email.get("reply_rate_pct", {}).get("sample_size", 0) >= MIN_ADAPTATION_SAMPLE
    )
    if mature and email_score > call_score:
        order = ["email", "call"]

    return {
        "category": category,
        "channel_order": order,
        "email": email,
        "ai_call": call,
        "evidence_mature": mature,
        "reason": (
            "evidence-backed ordering; eligibility remains independent"
            if mature else
            f"insufficient evidence for adaptation (minimum {MIN_ADAPTATION_SAMPLE} per relevant metric)"
        ),
    }


def learning_context(db: Session, lead: B2BLead) -> str:
    """Compact context for logs/prompts; never a permission decision."""
    r = recommendations_for_lead(db, lead)
    parts = [f"segment={r['category']}", f"order={','.join(r['channel_order'])}"]
    for channel in ("email", "ai_call"):
        block = r[channel]
        positive = block.get("positive_rate_pct") or block.get("reply_rate_pct")
        if positive:
            parts.append(
                f"{channel}_signal={positive['value']}%/{positive['sample_size']}"
            )
    return " | ".join(parts)
