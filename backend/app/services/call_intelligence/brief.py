"""Pre-dial call brief for the voice agent.

build_call_brief(db, lead) -> dict with:
  skip / skip_reasons   refuse this dial (never permits one)
  not_before            requested callback time, when in the future
  prior_calls           this lead's previous call results (newest first)
  avoid_openers         openers already used with this lead
  handle_objections     objections this lead raised before + how to handle
  segment               segment lessons (only past minimum-sample thresholds)
  opener_variant/...    epsilon-greedy selection among approved variants
  prompt_block          compact text for the agent's system prompt

The brief is advisory for the conversation; the only thing it can change
about *whether* a call happens is to refuse it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.call_intelligence import lessons as L
from app.services.call_intelligence import variants as V
from app.services.call_intelligence.models import CallResult, results_table_ready
from app.services.call_intelligence.standing_rules import call_blocked
from app.services.call_intelligence.taxonomy import STOP_OUTCOMES

IST = timezone(timedelta(hours=5, minutes=30))

# Truthful, CALL_CONSTRAINTS-compatible handling notes. No prices, no promises.
OBJECTION_PLAYBOOK = {
    "PRICE": "They raised price before. Never quote a figure; offer to have the team share current commercial terms.",
    "EXISTING_SUPPLIER": "They already have a supplier. Do not criticise it; ask if they would compare our catalogue as a backup option.",
    "NO_NEED": "They said they do not need it. Ask once whether staff or guests drink coffee; if not, close politely.",
    "TIMING": "They were busy last time. First ask if now is a good moment; offer a time of their choice.",
    "NOT_DECISION_MAKER": "Last time you reached someone who does not decide. Early on, ask who handles coffee purchase and when to reach them.",
    "TRUST_AI": "They questioned the AI call. Confirm you are an AI assistant for Purity Beans and offer a callback from the team.",
    "MOQ": "They asked about quantities. Never commit a quantity; offer the team for that.",
}
_LEGACY_STOP = frozenset({"NOT_INTERESTED", "WRONG_PERSON", "WRONG_NUMBER", "OPT_OUT", "REJECTED"})


def _fmt_ist(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%d %b %H:%M IST")


def prior_results(db, lead_id: int, limit: int = 5) -> list[CallResult]:
    if not results_table_ready(db):
        return []
    return (db.query(CallResult)
            .filter(CallResult.lead_id == lead_id, CallResult.status == "FINAL")
            .order_by(CallResult.id.desc()).limit(limit).all())


def build_call_brief(db, lead, *, now: datetime | None = None,
                     rows: list[CallResult] | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    reasons: list[str] = []
    blocked = call_blocked(lead)
    if blocked:
        reasons.append(blocked)
    if getattr(lead, "do_not_call", False):
        reasons.append("do_not_call is set on this lead")

    prior = prior_results(db, lead.id)
    prior_view = [{
        "at": _fmt_ist(r.started_at or r.ended_at), "outcome": r.outcome,
        "connected": r.connected, "opener_variant": r.opener_variant or "baseline",
        "objections": r.objections or [], "drop_off_stage": r.drop_off_stage,
        "summary": (r.summary or "")[:160], "confidence": r.confidence,
    } for r in prior]

    for r in prior:
        if (r.outcome or "") in STOP_OUTCOMES and r.confidence in {"OBSERVED", "HUMAN_CORRECTED", "INFERRED"}:
            reasons.append(f"previous call ended {r.outcome} ({_fmt_ist(r.ended_at or r.started_at) or 'earlier'})")
            break
    if any(r.rude_or_complaint for r in prior):
        reasons.append("caller complained / reacted negatively on a previous call")
    last_key = (getattr(lead, "call_outcome_last", "") or "").strip().upper()
    if last_key in _LEGACY_STOP and not any("previous call ended" in x for x in reasons):
        reasons.append(f"lead.call_outcome_last={last_key}")

    # "Call at the time they asked."
    callback_at = None
    cb_text = (getattr(lead, "founder_callback_window", "") or "").strip()
    for r in prior:
        if r.outcome == "CALLBACK_REQUESTED" and r.next_action_at:
            callback_at = r.next_action_at
            break
    if callback_at is None and cb_text:
        try:
            from app.services.founder_call_pipeline import parse_callback_datetime
            callback_at = parse_callback_datetime(cb_text, now)
        except Exception:  # noqa: BLE001
            callback_at = None
    not_before = callback_at if (callback_at and callback_at > now) else None
    if not_before:
        reasons.append(f"callback requested for {_fmt_ist(not_before)}; not before then")

    avoid = sorted({r.opener_variant or "baseline" for r in prior if r.connected})
    objections: list[str] = []
    for r in prior:
        for o in (r.objections or []):
            if o not in objections:
                objections.append(o)
    handle = [{"objection": o, "how": OBJECTION_PLAYBOOK.get(o, "Acknowledge it and offer the team.")}
              for o in objections]

    learn_rows = rows if rows is not None else L.learnable_rows(db)
    seg = L.segment_view(db, lead, rows=learn_rows)
    pick = V.select_opener(
        lead_id=lead.id, attempt=(getattr(lead, "ai_call_count", 0) or 0) + 1,
        rows=learn_rows, business_type=seg.get("business_type"), used_with_lead=avoid,
    )

    lines: list[str] = []
    if prior:
        last = prior_view[0]
        lines.append(f"Prior calls with this business: {len(prior)} (last {last['at'] or 'earlier'}: "
                     f"{(last['summary'] or last['outcome'] or '').rstrip('.')}).")
    if avoid and pick["opener_variant"] not in avoid:
        lines.append("Use a different opening angle than last time.")
    for h in handle[:3]:
        lines.append(h["how"])
    if cb_text:
        lines.append(f"They asked to be called: {cb_text[:60]}. Mention you are calling back as requested.")
    if seg.get("actionable"):
        bits = []
        if seg.get("best_time_window"):
            bits.append(f"best time window {seg['best_time_window']}")
        if seg.get("top_objections"):
            bits.append(f"common objection {seg['top_objections'][0][0]}")
        if bits:
            lines.append(f"Segment lesson (n={seg['sample']}): " + "; ".join(bits) + ".")
    if pick["instruction"]:
        lines.append(pick["instruction"])
    prompt_block = " ".join(lines)[:900]

    return {
        "version": 1,
        "lead_id": lead.id,
        "skip": bool(reasons),
        "skip_reasons": reasons,
        "not_before": not_before.isoformat() + "Z" if not_before else None,
        "prior_call_count": len(prior),
        "prior_calls": prior_view,
        "avoid_openers": avoid,
        "handle_objections": handle,
        "callback": {"requested": cb_text or None,
                     "at": callback_at.isoformat() + "Z" if callback_at else None},
        "segment": seg,
        "best_time_window": seg.get("best_time_window"),
        "segment_lessons_used": bool(seg.get("actionable")),
        "opener_variant": pick["opener_variant"],
        "script_variant": pick["script_variant"],
        "variant_selection": {k: pick[k] for k in ("mode", "candidates", "stats", "threshold_connected")},
        "opener_instruction": pick["instruction"],
        "prompt_block": prompt_block,
        "advisory_only": True,
    }


def dispatch_fields(brief: dict[str, Any], call_ref: str) -> dict[str, Any]:
    """Small, string-safe context keys for nuraveda_provider.place_call.

    Participant attributes are strings; nuraveda_provider JSON-encodes dicts.
    Kept compact so the LiveKit attribute payload stays small.
    """
    return {
        "call_ref": call_ref,
        "opener_variant": brief.get("opener_variant") or "baseline",
        "script_variant": brief.get("script_variant") or V.SCRIPT_VARIANT,
        "call_brief": brief.get("prompt_block") or "",
        "call_brief_json": {
            "v": 1,
            "prior_call_count": brief.get("prior_call_count", 0),
            "avoid_openers": brief.get("avoid_openers", []),
            "objections": [h["objection"] for h in brief.get("handle_objections", [])],
            "callback": (brief.get("callback") or {}).get("requested"),
            "opener_instruction": brief.get("opener_instruction") or "",
        },
    }
