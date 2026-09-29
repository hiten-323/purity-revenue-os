"""Segment aggregation of call results, with minimum-sample thresholds.

A rate is reported for every group, but a *lesson* (something the next call
acts on) is only emitted when the group has enough evidence:

  CALL_LESSON_MIN_ATTEMPTS   (default 30) dials before a connect-rate / time
                             window lesson;
  CALL_LESSON_MIN_CONNECTED  (default 10) connected calls before an interest /
                             DM / opener / objection lesson.

Reconciled rows (source=reconciler, LOW_CONFIDENCE) are counted separately
and never enter a rate: "we don't know what happened" is not a no-answer.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from app.services.call_intelligence.models import CallResult, results_table_ready
from app.services.call_intelligence.taxonomy import POSITIVE_OUTCOMES
from app.services.outreach_intelligence.experience_store import wilson_lower_bound

DIMENSIONS = ("business_type", "city", "hour_bucket", "language",
              "script_variant", "opener_variant")
LEARNABLE_CONFIDENCE = frozenset({"OBSERVED", "INFERRED", "HUMAN_CORRECTED"})


def min_attempts() -> int:
    return max(1, int(os.getenv("CALL_LESSON_MIN_ATTEMPTS", "30")))


def min_connected() -> int:
    return max(1, int(os.getenv("CALL_LESSON_MIN_CONNECTED", "10")))


def _rate(wins: int, n: int, threshold: int) -> dict[str, Any]:
    return {
        "wins": wins, "n": n,
        "rate": (wins / n) if n else None,
        "wilson_lb": wilson_lower_bound(wins, n) if n else None,
        "actionable": n >= threshold,
        "min_sample": threshold,
    }


def learnable_rows(db, *, days: int = 60, now: datetime | None = None) -> list[CallResult]:
    if not results_table_ready(db):
        return []
    since = (now or datetime.utcnow()) - timedelta(days=max(1, days))
    return (db.query(CallResult)
            .filter(CallResult.status == "FINAL",
                    CallResult.confidence.in_(tuple(LEARNABLE_CONFIDENCE)),
                    (CallResult.started_at >= since) | (CallResult.started_at.is_(None)))
            .all())


def summarize(rows: Iterable[CallResult]) -> dict[str, Any]:
    rows = list(rows)
    n = len(rows)
    connected = [r for r in rows if r.connected]
    c = len(connected)
    dm = sum(1 for r in connected if r.reached_decision_maker)
    interest = sum(1 for r in connected if (r.outcome or "") in POSITIVE_OUTCOMES)
    objections = Counter(o for r in connected for o in (r.objections or []))
    drop = Counter(r.drop_off_stage for r in connected
                   if r.drop_off_stage and (r.outcome or "") not in POSITIVE_OUTCOMES)
    drop_turns = Counter(r.drop_off_turn for r in connected
                         if r.drop_off_turn is not None and (r.outcome or "") not in POSITIVE_OUTCOMES)
    outcomes = Counter(r.outcome or "UNKNOWN" for r in rows)
    return {
        "attempts": n, "connected": c,
        "connect_rate": _rate(c, n, min_attempts()),
        "dm_reach_rate": _rate(dm, c, min_connected()),
        "interest_rate": _rate(interest, c, min_connected()),
        "outcomes": dict(outcomes.most_common()),
        "top_objections": objections.most_common(5),
        "drop_off_stages": drop.most_common(5),
        "drop_off_turns": drop_turns.most_common(5),
    }


def aggregate(db, *, days: int = 60, now: datetime | None = None,
              rows: list[CallResult] | None = None) -> dict[str, Any]:
    rows = rows if rows is not None else learnable_rows(db, days=days, now=now)
    by_dim: dict[str, dict[str, Any]] = {}
    for dim in DIMENSIONS:
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[str(getattr(r, dim) or "unknown")].append(r)
        by_dim[dim] = {k: summarize(v) for k, v in sorted(groups.items())}
    # business_type x opener_variant: what the brief actually picks from.
    combo: dict[str, list] = defaultdict(list)
    for r in rows:
        combo[f"{r.business_type or 'unknown'}|{r.opener_variant or 'baseline'}"].append(r)
    return {
        "overall": summarize(rows),
        "by": by_dim,
        "by_business_type_opener": {k: summarize(v) for k, v in sorted(combo.items())},
        "thresholds": {"min_attempts": min_attempts(), "min_connected": min_connected()},
        "window_days": days,
        "lessons": derive_segment_lessons(by_dim),
    }


def _best(groups: dict[str, dict], metric: str, *, exclude=("unknown",)) -> tuple[str, dict] | None:
    cands = [(k, g[metric]) for k, g in groups.items()
             if k not in exclude and g[metric]["actionable"] and g[metric]["wilson_lb"] is not None]
    if len(cands) < 2:
        return None
    cands.sort(key=lambda kv: kv[1]["wilson_lb"], reverse=True)
    best, runner = cands[0], cands[1]
    # Only a lesson when the best is clearly ahead: its lower bound beats the
    # runner-up's point estimate. Otherwise it is noise.
    if best[1]["wilson_lb"] > (runner[1]["rate"] or 0):
        return best
    return None


def derive_segment_lessons(by_dim: dict[str, dict[str, dict]]) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for dim, metric, label in (
        ("hour_bucket", "connect_rate", "best time window to connect"),
        ("opener_variant", "interest_rate", "best opener for interest"),
        ("script_variant", "interest_rate", "best script for interest"),
        ("language", "interest_rate", "best language for interest"),
    ):
        hit = _best(by_dim.get(dim, {}), metric)
        if hit:
            k, m = hit
            lessons.append({"dimension": dim, "value": k, "metric": metric,
                            "rate": m["rate"], "wilson_lb": m["wilson_lb"],
                            "n": m["n"], "lesson": f"{label}: {k}"})
    for bt, g in by_dim.get("business_type", {}).items():
        if g["interest_rate"]["actionable"] and g["top_objections"]:
            obj, cnt = g["top_objections"][0]
            lessons.append({"dimension": "business_type", "value": bt,
                            "metric": "top_objection", "objection": obj,
                            "count": cnt, "n": g["connected"],
                            "lesson": f"{bt}: most common objection is {obj}"})
        stages = g.get("drop_off_stages") or []
        if g["interest_rate"]["actionable"] and stages and stages[0][0] == "OPENER":
            lessons.append({"dimension": "business_type", "value": bt,
                            "metric": "drop_off", "stage": "OPENER",
                            "count": stages[0][1], "n": g["connected"],
                            "lesson": f"{bt}: most unsuccessful calls end during the opener"})
    return lessons


def segment_view(db, lead, *, rows: list[CallResult] | None = None,
                 days: int = 60) -> dict[str, Any]:
    """Lessons relevant to one lead's segment (business type, fallback: all)."""
    rows = rows if rows is not None else learnable_rows(db, days=days)
    bt = (getattr(lead, "segment", None) or getattr(lead, "division", None) or "").strip().lower() or None
    seg_rows = [r for r in rows if bt and r.business_type == bt]
    scope = "business_type" if len(seg_rows) >= min_attempts() else "all"
    use = seg_rows if scope == "business_type" else rows
    groups_hour: dict[str, list] = defaultdict(list)
    groups_open: dict[str, list] = defaultdict(list)
    for r in use:
        groups_hour[r.hour_bucket or "unknown"].append(r)
        groups_open[r.opener_variant or "baseline"].append(r)
    hours = {k: summarize(v) for k, v in groups_hour.items()}
    openers = {k: summarize(v) for k, v in groups_open.items()}
    best_hour = _best(hours, "connect_rate")
    best_opener = _best(openers, "interest_rate", exclude=())
    summ = summarize(use)
    top_obj = summ["top_objections"] if summ["interest_rate"]["actionable"] else []
    return {
        "scope": scope, "business_type": bt, "sample": summ["attempts"],
        "connected": summ["connected"],
        "best_time_window": best_hour[0] if best_hour else None,
        "best_opener": best_opener[0] if best_opener else None,
        "opener_stats": {k: v["interest_rate"] for k, v in openers.items()},
        "top_objections": top_obj,
        "thresholds": {"min_attempts": min_attempts(), "min_connected": min_connected()},
        "actionable": bool(best_hour or best_opener or top_obj),
    }
