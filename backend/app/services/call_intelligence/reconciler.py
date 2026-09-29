"""Sweeper: no lead stays call_status='CALLING' past N minutes.

For every lead still CALLING whose last dial is older than
``CALL_RECONCILER_STALE_MINUTES`` (default 30), derive a terminal state from
the evidence we actually hold, in this order:

  1. OUTCOME_RECORDED   an outcome for the latest dial IS in call_history /
                        call_outcome_last, only call_status was never cleared
                        (the pre-fix record_ai_outcome never touched it);
  2. SIDECAR_<REASON>   the Nuraveda sidecar's own disposition for the call
                        (no_answer / busy / dispatch_error ...), when an
                        evidence export is supplied (see
                        integrations/nuraveda-voice/export_call_evidence.mjs);
  3. OPEN_RESULT        a CallResult row exists (e.g. answered_at seen) --
                        answered but never reported -> UNKNOWN, connected;
  4. UNKNOWN_NO_RESULT  nothing: we do not know what happened.

It NEVER moves outreach_stage and never invents a business decision: the FSM
stage is left for record_ai_outcome / the founder. Reconciled rows are
LOW_CONFIDENCE (source=reconciler) and are excluded from learning rates.

Dry-run is pure SELECTs (works on a ``mode=ro`` SQLite connection).
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import inspect, text

from app.services.call_intelligence.taxonomy import (
    ENGINE_TERMINATIONS, SIDECAR_DISPOSITIONS, result_outcome_for,
)

MODES = ("off", "dry_run", "write")


def stale_minutes() -> int:
    try:
        return max(5, int(os.getenv("CALL_RECONCILER_STALE_MINUTES", "30")))
    except ValueError:
        return 30


def mode() -> str:
    m = (os.getenv("CALL_RECONCILER_MODE", "dry_run") or "dry_run").strip().lower()
    return m if m in MODES else "dry_run"


def _as_dt(v):
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", ""))
    except Exception:  # noqa: BLE001
        return None


def _columns(conn, table: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:  # noqa: BLE001
        return set()


def find_stuck(conn, *, older_than_minutes: int, now: datetime,
               limit: int | None = None) -> list[dict[str, Any]]:
    cols = _columns(conn, "b2b_leads")
    want = ["id", "company", "city", "segment", "division", "call_status",
            "outreach_stage", "last_call_date", "call_outcome_last",
            "ai_call_count", "call_summary", "do_not_call", "phone"]
    sel = ", ".join(c for c in want if c in cols)
    cutoff = now - timedelta(minutes=older_than_minutes)
    sql = (f"SELECT {sel} FROM b2b_leads WHERE call_status = 'CALLING' "
           "AND (last_call_date IS NULL OR last_call_date < :cutoff) ORDER BY id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(text(sql), {"cutoff": cutoff}).mappings().all()
    return [dict(r) for r in rows]


def _history(conn, lead_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(text(
        "SELECT id, call_date, status, call_status, summary FROM call_history "
        "WHERE lead_id = :lid ORDER BY id"), {"lid": lead_id}).mappings().all()
    return [dict(r) for r in rows]


def _open_result(conn, lead_id: int, has_results: bool) -> dict[str, Any] | None:
    if not has_results:
        return None
    r = conn.execute(text(
        "SELECT id, call_id, answered_at, status FROM call_results "
        "WHERE lead_id = :lid ORDER BY id DESC LIMIT 1"), {"lid": lead_id}).mappings().first()
    return dict(r) if r else None


def decide(lead: dict[str, Any], history: list[dict[str, Any]],
           open_result: dict[str, Any] | None = None,
           sidecar: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pure decision for one stuck lead. Returns proposed terminal state."""
    last_dial_idx = max((i for i, h in enumerate(history)
                         if (h.get("status") or "") == "AI_CALL_ATTEMPTED"), default=None)
    concluded_idx = [i for i, h in enumerate(history)
                     if (h.get("status") or "").startswith("AI_")
                     and h.get("status") != "AI_CALL_ATTEMPTED"
                     and (h.get("call_status") or "") == "COMPLETED"]
    latest_outcome_idx = concluded_idx[-1] if concluded_idx else None
    open_dials = [h for h in history if (h.get("call_status") or "") == "CALLING"]

    # 1. An outcome for the most recent dial exists.
    outcome_after_dial = latest_outcome_idx is not None and (
        last_dial_idx is None or latest_outcome_idx > last_dial_idx)
    if outcome_after_dial or (not open_dials and lead.get("call_outcome_last")):
        key = (history[latest_outcome_idx]["status"][3:] if latest_outcome_idx is not None
               else str(lead.get("call_outcome_last") or "")).upper()
        return {"state": "OUTCOME_RECORDED", "fsm_key": key,
                "result_outcome": result_outcome_for(key),
                "reason": "outcome recorded for the latest dial; call_status never cleared",
                "open_dials": len(open_dials)}

    # 2. Sidecar disposition.
    if sidecar:
        disp = str(sidecar.get("disposition") or sidecar.get("outcome") or "").lower()
        user_turns = int(sidecar.get("user_turns") or 0)
        term = SIDECAR_DISPOSITIONS.get(disp)
        if user_turns > 0 and term in {None, "NO_ANSWER", "TIMEOUT"}:
            return {"state": "SIDECAR_ANSWERED_NO_OUTCOME", "termination": "HANGUP_NO_OUTCOME",
                    "fsm_key": "FAILED", "result_outcome": "UNKNOWN",
                    "reason": f"sidecar shows {user_turns} caller turn(s) but no outcome",
                    "open_dials": len(open_dials)}
        if term:
            fsm = ENGINE_TERMINATIONS.get(term, "FAILED")
            return {"state": f"SIDECAR_{term}", "termination": term, "fsm_key": fsm,
                    "result_outcome": result_outcome_for(fsm),
                    "reason": f"sidecar disposition={disp}", "open_dials": len(open_dials)}

    # 3. We saw the call answered but got nothing else.
    if open_result and open_result.get("answered_at"):
        return {"state": "ANSWERED_NO_OUTCOME", "termination": "HANGUP_NO_OUTCOME",
                "fsm_key": None, "result_outcome": "UNKNOWN",
                "reason": "call_results shows answered_at but no outcome",
                "open_dials": len(open_dials)}

    # 4. Nothing.
    return {"state": "UNKNOWN_NO_RESULT", "fsm_key": None, "result_outcome": "UNKNOWN",
            "reason": "no outcome, no sidecar disposition, no answer evidence",
            "open_dials": len(open_dials)}


def reconcile_stuck_calls(db_or_conn, *, older_than_minutes: int | None = None,
                          dry_run: bool = True, now: datetime | None = None,
                          limit: int | None = None,
                          sidecar_evidence: dict[int, dict] | None = None,
                          sample_size: int = 15) -> dict[str, Any]:
    """Report (dry_run) or apply terminal states for stale CALLING leads."""
    now = now or datetime.utcnow()
    minutes = older_than_minutes or stale_minutes()
    is_session = hasattr(db_or_conn, "query")
    conn = db_or_conn.connection() if is_session else db_or_conn
    has_results = bool(inspect(conn).has_table("call_results"))
    stuck = find_stuck(conn, older_than_minutes=minutes, now=now, limit=limit)

    by_state: Counter = Counter()
    by_stage: Counter = Counter()
    by_age: Counter = Counter()
    by_state_stage: Counter = Counter()
    open_dial_rows = 0
    samples: list[dict[str, Any]] = []
    decisions: list[tuple[dict, dict]] = []
    for lead in stuck:
        hist = _history(conn, lead["id"])
        d = decide(lead, hist, _open_result(conn, lead["id"], has_results),
                   (sidecar_evidence or {}).get(lead["id"]))
        decisions.append((lead, d))
        by_state[d["state"]] += 1
        by_stage[lead.get("outreach_stage") or "NONE"] += 1
        by_state_stage[f"{d['state']}|{lead.get('outreach_stage') or 'NONE'}"] += 1
        open_dial_rows += d.get("open_dials", 0)
        last = _as_dt(lead.get("last_call_date"))
        age_h = ((now - last).total_seconds() / 3600.0) if last else None
        bucket = ("unknown" if age_h is None else "<1h" if age_h < 1 else "1-24h" if age_h < 24
                  else "1-3d" if age_h < 72 else "3-7d" if age_h < 168 else ">7d")
        by_age[bucket] += 1
        if len(samples) < sample_size and (d["state"] != "UNKNOWN_NO_RESULT" or len(samples) < 5):
            samples.append({"lead_id": lead["id"], "stage": lead.get("outreach_stage"),
                            "last_call_date": str(lead.get("last_call_date")),
                            "ai_call_count": lead.get("ai_call_count"),
                            "call_outcome_last": lead.get("call_outcome_last"), **d})

    applied = 0
    if not dry_run and is_session and decisions:
        applied = _apply(db_or_conn, decisions, now=now)

    return {
        "mode": "dry_run" if dry_run else "write",
        "older_than_minutes": minutes,
        "now_utc": now.isoformat(),
        "stuck_calling_leads": len(stuck),
        "proposed_terminal_state": dict(by_state.most_common()),
        "by_outreach_stage": dict(by_stage.most_common()),
        "by_state_and_stage": dict(by_state_stage.most_common()),
        "by_age_since_last_dial": dict(by_age.most_common()),
        "open_call_history_rows_to_close": open_dial_rows,
        "call_results_table_present": has_results,
        "sidecar_evidence_rows": len(sidecar_evidence or {}),
        "applied": applied,
        "samples": samples,
        "note": ("outreach_stage is never changed by the reconciler; only call_status, "
                 "open call_history rows and a LOW_CONFIDENCE call_results row."),
    }


def _apply(db, decisions, *, now: datetime) -> int:
    from app.database.database import commit_with_retry
    from app.models.models import B2BLead
    from app.services.call_intelligence.capture import finalize_call

    n = 0
    for lead_row, d in decisions:
        lead = db.query(B2BLead).filter(B2BLead.id == lead_row["id"]).first()
        if lead is None or (lead.call_status or "") != "CALLING":
            continue  # a real outcome landed meanwhile
        state = d["state"]
        if state == "OUTCOME_RECORDED":
            # The outcome itself was observed (it is in call_history); only
            # the writeback was missing. source=reconciler keeps the orphaned
            # reservation rows' status untouched (RECONCILED, not re-labelled).
            finalize_call(db, lead, fsm_key=d["fsm_key"], source="reconciler",
                          summary=lead.call_summary, now=now,
                          meta={"reconciled_state": state})
        elif state.startswith("SIDECAR_") or state == "ANSWERED_NO_OUTCOME":
            finalize_call(db, lead, fsm_key=d.get("fsm_key"),
                          termination_reason=d.get("termination"), source="reconciler",
                          now=now, meta={"reconciled_state": state},
                          result_override={"confidence": "LOW_CONFIDENCE"})
        else:
            finalize_call(db, lead, fsm_key=None, termination_reason=None,
                          source="reconciler", now=now,
                          meta={"reconciled_state": state},
                          result_override={"outcome": "UNKNOWN", "connected": None,
                                           "confidence": "LOW_CONFIDENCE",
                                           "termination_reason": "UNKNOWN_NO_RESULT"})
            lead.call_status = "UNKNOWN_NO_RESULT"
        n += 1
        if n % 100 == 0:
            commit_with_retry(db)
    commit_with_retry(db)
    return n


def run_from_worker(db) -> dict[str, Any]:
    m = mode()
    if m == "off":
        return {"mode": "off"}
    res = reconcile_stuck_calls(db, dry_run=(m != "write"), sample_size=3)
    res.pop("samples", None)
    return res
