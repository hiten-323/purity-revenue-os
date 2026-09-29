"""Call result capture + call learning loop (app.services.call_intelligence).

Every termination path must leave call_status != CALLING and write one
FINAL CallResult; the reconciler must only report in dry-run; lessons need
minimum samples; the brief must refuse do-not-call leads and adapt the next
call. No test here dials, sends or talks to a provider: voice_router.place_call
is replaced by a recorder wherever the dial path is exercised.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.database import Base, get_db
from app.models.models import B2BLead, CallHistory
from app.services import founder_call_pipeline as pipeline
from app.services.call_intelligence import (
    brief as B, capture as C, classifier as K, lessons as L, reconciler as R,
    standing_rules as S, taxonomy as T, variants as V,
)
from app.services.call_intelligence.models import CallResult
from conftest import memory_engine

SECRET = "test-admin-secret"
H = {"X-Api-Admin-Secret": SECRET}


@pytest.fixture
def db():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _lead(db, **kw):
    kw.setdefault("phone", "9876543210")
    kw.setdefault("company", f"Test Cafe {kw['phone'][-4:]}-{db.query(B2BLead).count()}")
    kw.setdefault("segment", "cafe")
    kw.setdefault("city", "Bathinda")
    lead = B2BLead(**kw)
    db.add(lead)
    db.commit()
    return lead


def _dialled(db, lead, *, minutes_ago=60, call_ref=None, opener="baseline"):
    """State the dial path leaves behind: stage AI_CALL_ATTEMPTED, CALLING."""
    if pipeline.stage_of(lead) == pipeline.ELIGIBLE:
        pipeline.advance(lead, db, pipeline.AI_CALL_ATTEMPTED, note="test dial")
    lead.call_status = "CALLING"
    lead.ai_call_count = (lead.ai_call_count or 0) + 1
    lead.last_call_date = datetime.utcnow() - timedelta(minutes=minutes_ago)
    db.add(CallHistory(lead_id=lead.id, call_date=lead.last_call_date,
                       status="AI_CALL_ATTEMPTED", call_status="CALLING",
                       summary="dispatched"))
    if call_ref:
        C.open_call_attempt(db, lead, call_ref=call_ref, provider="nuraveda",
                            provider_call_id="sc-1", opener_variant=opener,
                            started_at=lead.last_call_date)
    db.commit()
    return lead


def _client(monkeypatch, db):
    from app.api import founder_router
    from app.api.outreach_intelligence_router import router as oi_router
    monkeypatch.setenv("API_ADMIN_SECRET", SECRET)
    monkeypatch.delenv("GATEWAY_ADMIN_SECRET", raising=False)
    app = FastAPI()
    app.include_router(founder_router.router, prefix="/api/v1")
    app.include_router(oi_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _final(db, lead):
    return (db.query(CallResult).filter(CallResult.lead_id == lead.id,
                                        CallResult.status == "FINAL")
            .order_by(CallResult.id.desc()).first())


# ── taxonomy ────────────────────────────────────────────────────────────────

def test_sip_codes_map_to_terminations():
    assert T.termination_from_sip(486) == "BUSY"
    assert T.termination_from_sip("480") == "NO_ANSWER"
    assert T.termination_from_sip(403) == "SIP_ERROR"
    assert T.termination_from_sip(200) is None
    assert T.termination_from_sip("x") is None


def test_terminal_status_is_never_calling():
    for o in T.RESULT_OUTCOMES:
        for conn in (True, False, None):
            for rec in (True, False):
                assert T.terminal_call_status_for(o, connected=conn, reconciled=rec) != "CALLING"


# ── classifier ──────────────────────────────────────────────────────────────

def test_classifier_reads_a_hinglish_interested_call():
    turns = [
        {"role": "assistant", "text": "Namaste, main Purity Beans ki taraf se AI assistant hoon."},
        {"role": "user", "text": "haan ji boliye, main owner hoon"},
        {"role": "assistant", "text": "Kya aap instant coffee use karte hain?"},
        {"role": "user", "text": "haan, rate kya hai? price zyada toh nahi?"},
        {"role": "assistant", "text": "Team current terms share karegi."},
        {"role": "user", "text": "theek hai catalogue bhej do whatsapp pe"},
    ]
    r = K.classify_call(fsm_key="WHATSAPP_OPT_IN", turns=turns)
    assert r["outcome"] == "CATALOGUE_REQUESTED"
    assert r["connected"] is True
    assert r["reached_decision_maker"] is True
    assert "PRICE" in r["objections"]
    assert r["language"] == "hi-Latn"
    assert r["confidence"] == "OBSERVED"
    assert r["turn_count"] == 6 and r["user_turn_count"] == 3


def test_classifier_never_invents_without_evidence():
    r = K.classify_call(fsm_key=None)
    assert r["outcome"] == "UNKNOWN"
    assert r["objections"] == []
    assert r["reached_decision_maker"] is None
    assert r["confidence"] == "LOW_CONFIDENCE"


def test_classifier_session_closed_summary_is_a_connected_hangup():
    r = K.classify_call(fsm_key="FAILED",
                        summary="session closed after 3 turn(s) with no outcome recorded")
    assert r["connected"] is True
    assert r["termination_reason"] == "HANGUP_NO_OUTCOME"


def test_classifier_flags_rudeness():
    r = K.classify_call(fsm_key="OPT_OUT", transcript="user: bakwas band karo, dobara call mat karna")
    assert r["outcome"] == "DO_NOT_CALL"
    assert r["rude_or_complaint"] is True


# ── termination paths: every one leaves CALLING ─────────────────────────────

@pytest.mark.parametrize("reason,sip,expect_outcome,expect_status", [
    ("NO_ANSWER", None, "NO_ANSWER", "NO_ANSWER"),
    ("BUSY", None, "BUSY", "BUSY"),
    ("", 486, "BUSY", "BUSY"),
    ("VOICEMAIL", None, "VOICEMAIL", "VOICEMAIL"),
    ("FAILED", None, "FAILED", "FAILED"),
    ("SIP_ERROR", 403, "FAILED", "FAILED"),
    ("AGENT_CRASH", None, "FAILED", "FAILED"),
    ("TIMEOUT", None, "FAILED", "FAILED"),
    ("STUCK_DISPATCH", None, "NO_ANSWER", "NO_ANSWER"),
])
def test_engine_termination_paths_write_terminal_state(monkeypatch, db, reason, sip,
                                                       expect_outcome, expect_status):
    lead = _dialled(db, _lead(db), call_ref="ref-1")
    client = _client(monkeypatch, db)
    r = client.post("/api/v1/founder/ai-call-status", headers=H,
                    json={"lead_id": lead.id, "reason": reason, "sip_status_code": sip,
                          "call_ref": "ref-1"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert lead.call_status == expect_status
    assert r.json()["fsm_applied"] is True
    assert pipeline.stage_of(lead) == pipeline.AI_NO_ANSWER
    row = _final(db, lead)
    assert row.call_id == "ref-1" and row.outcome == expect_outcome
    assert row.connected is False and row.source == "engine"
    # the reservation call_history row is concluded, not left CALLING
    assert not db.query(CallHistory).filter(CallHistory.lead_id == lead.id,
                                            CallHistory.call_status == "CALLING").count()


def test_unknown_engine_reason_is_refused_not_guessed(monkeypatch, db):
    lead = _dialled(db, _lead(db))
    r = _client(monkeypatch, db).post("/api/v1/founder/ai-call-status", headers=H,
                                      json={"lead_id": lead.id, "reason": "MAYBE"})
    assert r.status_code == 400


def test_answered_call_with_outcome_leaves_calling_and_records_details(monkeypatch, db):
    lead = _dialled(db, _lead(db), call_ref="ref-2", opener="question_first")
    client = _client(monkeypatch, db)
    now = datetime.utcnow()
    r = client.post("/api/v1/founder/ai-call-outcome", headers=H, json={
        "lead_id": lead.id, "outcome": "CALLBACK_REQUESTED",
        "summary": "Owner asked to call back tomorrow evening", "callback_window": "tomorrow 6pm",
        "call_ref": "ref-2", "answered_at": (now - timedelta(seconds=95)).isoformat(),
        "ended_at": now.isoformat(),
        "turns": [{"role": "assistant", "text": "Namaste, AI assistant from Purity Beans"},
                  {"role": "user", "text": "abhi busy hoon, kal shaam ko call karna"}],
    })
    assert r.status_code == 200, r.text
    db.expire_all()
    assert lead.call_status == "COMPLETED"
    row = _final(db, lead)
    assert row.call_id == "ref-2"
    assert row.outcome == "CALLBACK_REQUESTED" and row.connected is True
    assert row.opener_variant == "question_first"
    assert 90 <= row.duration_seconds <= 100
    assert row.next_action and row.next_action_at is not None
    assert "TIMING" in (row.objections or [])
    assert row.summary


def test_close_handler_failed_report_is_a_connected_hangup(monkeypatch, db):
    lead = _dialled(db, _lead(db))
    r = _client(monkeypatch, db).post("/api/v1/founder/ai-call-outcome", headers=H, json={
        "lead_id": lead.id, "outcome": "FAILED",
        "summary": "session closed after 2 turn(s) with no outcome recorded"})
    assert r.status_code == 200
    db.expire_all()
    assert lead.call_status != "CALLING"
    row = _final(db, lead)
    assert row.connected is True and row.termination_reason == "HANGUP_NO_OUTCOME"


def test_a_second_attempts_identical_outcome_is_recorded_not_swallowed(monkeypatch, db):
    """Root cause #3: retries re-dial AI_NO_ANSWER leads, and the old
    idempotency check dropped the second NO_ANSWER as a duplicate."""
    lead = _lead(db)
    client = _client(monkeypatch, db)
    _dialled(db, lead, call_ref="a1")
    assert client.post("/api/v1/founder/ai-call-status", headers=H,
                       json={"lead_id": lead.id, "reason": "NO_ANSWER", "call_ref": "a1"}).status_code == 200
    db.expire_all()
    count_after_first = lead.ai_call_count
    _dialled(db, lead, call_ref="a2")
    r = client.post("/api/v1/founder/ai-call-outcome", headers=H,
                    json={"lead_id": lead.id, "outcome": "NO_ANSWER", "call_ref": "a2"})
    assert r.json()["status"] == "recorded"
    db.expire_all()
    assert lead.call_status == "NO_ANSWER"
    assert lead.ai_call_count == count_after_first + 2  # dial + outcome, as today
    assert {r.call_id for r in db.query(CallResult).filter(CallResult.status == "FINAL")} == {"a1", "a2"}
    # and an exact retry of the same report is still a no-op
    again = client.post("/api/v1/founder/ai-call-outcome", headers=H,
                        json={"lead_id": lead.id, "outcome": "NO_ANSWER", "call_ref": "a2"})
    assert again.json()["status"] == "already_recorded"


def test_legacy_retry_without_call_ref_is_still_idempotent(monkeypatch, db):
    lead = _dialled(db, _lead(db))
    client = _client(monkeypatch, db)
    body = {"lead_id": lead.id, "outcome": "NOT_INTERESTED", "summary": "no thanks"}
    assert client.post("/api/v1/founder/ai-call-outcome", headers=H, json=body).json()["status"] == "recorded"
    assert client.post("/api/v1/founder/ai-call-outcome", headers=H, json=body).json()["status"] == "already_recorded"
    assert db.query(CallResult).filter(CallResult.lead_id == lead.id).count() == 1


def test_late_engine_report_for_concluded_call_does_not_add_an_attempt(monkeypatch, db):
    lead = _dialled(db, _lead(db), call_ref="z1")
    client = _client(monkeypatch, db)
    client.post("/api/v1/founder/ai-call-outcome", headers=H,
                json={"lead_id": lead.id, "outcome": "INTERESTED", "call_ref": "z1"})
    db.expire_all()
    n = lead.ai_call_count
    r = client.post("/api/v1/founder/ai-call-status", headers=H,
                    json={"lead_id": lead.id, "reason": "NO_ANSWER", "call_ref": "z1"})
    assert r.json()["status"] == "already_recorded"
    db.expire_all()
    assert lead.ai_call_count == n and pipeline.stage_of(lead) == pipeline.AI_INTEREST_DETECTED


def test_capture_failure_never_blocks_the_outcome(monkeypatch, db):
    lead = _dialled(db, _lead(db))

    def boom(*a, **k):
        raise RuntimeError("capture down")
    monkeypatch.setattr(C, "finalize_call", boom)
    pipeline.record_ai_outcome(lead, db, "NOT_INTERESTED", summary="no")
    db.commit()
    assert lead.call_status == "COMPLETED"
    assert pipeline.stage_of(lead) == pipeline.AI_NOT_INTERESTED


# ── reconciler / sweeper ────────────────────────────────────────────────────

def _stuck_fixture(db):
    a = _dialled(db, _lead(db, company="A"), minutes_ago=600)          # nothing ever came back
    b = _dialled(db, _lead(db, company="B"), minutes_ago=600)
    db.add(CallHistory(lead_id=b.id, call_date=datetime.utcnow() - timedelta(minutes=590),
                       status="AI_NOT_INTERESTED", call_status="COMPLETED", summary="said no"))
    b.call_outcome_last = "NOT_INTERESTED"
    c = _dialled(db, _lead(db, company="C"), minutes_ago=5)            # in flight: too fresh
    d = _dialled(db, _lead(db, company="D"), minutes_ago=600)
    db.commit()
    return a, b, c, d


def test_reconciler_dry_run_reports_and_writes_nothing(db):
    a, b, c, d = _stuck_fixture(db)
    res = R.reconcile_stuck_calls(db, dry_run=True, older_than_minutes=30,
                                  sidecar_evidence={d.id: {"disposition": "busy"}})
    assert res["mode"] == "dry_run" and res["applied"] == 0
    assert res["stuck_calling_leads"] == 3
    assert res["proposed_terminal_state"] == {"UNKNOWN_NO_RESULT": 1, "OUTCOME_RECORDED": 1,
                                              "SIDECAR_BUSY": 1}
    db.expire_all()
    assert all(x.call_status == "CALLING" for x in (a, b, c, d))
    assert db.query(CallResult).count() == 0


def test_reconciler_write_mode_clears_calling_but_not_stage(db):
    a, b, c, d = _stuck_fixture(db)
    stages = {x.id: pipeline.stage_of(x) for x in (a, b, c, d)}
    res = R.reconcile_stuck_calls(db, dry_run=False, older_than_minutes=30,
                                  sidecar_evidence={d.id: {"disposition": "busy"}})
    assert res["applied"] == 3
    db.expire_all()
    assert a.call_status == "UNKNOWN_NO_RESULT"
    assert b.call_status == "COMPLETED"
    assert d.call_status == "BUSY"
    assert c.call_status == "CALLING"                 # fresh call left alone
    assert {x.id: pipeline.stage_of(x) for x in (a, b, c, d)} == stages
    ra = _final(db, a)
    assert ra.outcome == "UNKNOWN" and ra.confidence == "LOW_CONFIDENCE" and ra.source == "reconciler"
    # reconciled unknowns are never learned from
    assert a.id not in {r.lead_id for r in L.learnable_rows(db)}
    # idempotent
    assert R.reconcile_stuck_calls(db, dry_run=False, older_than_minutes=30)["applied"] == 0


def test_late_real_report_after_reconcile_still_lands(monkeypatch, db):
    lead = _dialled(db, _lead(db), minutes_ago=600, call_ref="late-1")
    R.reconcile_stuck_calls(db, dry_run=False, older_than_minutes=30)
    db.expire_all()
    assert lead.call_status == "UNKNOWN_NO_RESULT"
    r = _client(monkeypatch, db).post("/api/v1/founder/ai-call-status", headers=H,
                                      json={"lead_id": lead.id, "reason": "NO_ANSWER",
                                            "call_ref": "late-1"})
    assert r.json()["fsm_applied"] is True
    db.expire_all()
    row = _final(db, lead)
    assert row.call_id == "late-1" and row.outcome == "NO_ANSWER" and row.source == "engine"
    assert lead.call_status == "NO_ANSWER"


def test_worker_reconciler_defaults_to_dry_run(monkeypatch, db):
    monkeypatch.delenv("CALL_RECONCILER_MODE", raising=False)
    a = _dialled(db, _lead(db), minutes_ago=600)
    res = R.run_from_worker(db)
    assert res["mode"] == "dry_run" and res["stuck_calling_leads"] == 1
    db.expire_all()
    assert a.call_status == "CALLING"
    monkeypatch.setenv("CALL_RECONCILER_MODE", "off")
    assert R.run_from_worker(db) == {"mode": "off"}


def test_backfill_script_dry_run_is_read_only(tmp_path):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine

    path = tmp_path / "prod_copy.db"
    eng = create_engine(f"sqlite:///{path}")
    # the production DB today: every table except the new call_results
    Base.metadata.create_all(eng, tables=[t for t in Base.metadata.sorted_tables
                                          if t.name != "call_results"])
    s = sessionmaker(bind=eng)()
    _dialled(s, _lead(s), minutes_ago=6000)
    s.close()
    eng.dispose()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    spec = importlib.util.spec_from_file_location(
        "backfill_stuck_calls", Path(__file__).parents[1] / "scripts" / "backfill_stuck_calls.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "report.json"
    assert mod.main(["--db", str(path), "--json", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["mode"] == "dry_run" and rep["proposed_terminal_state"] == {"UNKNOWN_NO_RESULT": 1}
    assert rep["call_results_table_present"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    # --apply alone is not enough
    assert mod.main(["--db", str(path), "--apply"]) == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# ── standing rules ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("phone", ["8591977190", "+91 85919 77190", "091-8591977190", "918591977190"])
def test_never_dial_number(phone):
    assert S.number_blocked(phone)


def test_do_not_call_businesses():
    assert S.business_blocked("Salt'n Peppr", "Faridkot")
    assert S.business_blocked("SALT N PEPPR restaurant", "faridkot")
    assert S.business_blocked("SPOT ON Hotel KK Residency", "Sri Muktsar Sahib")
    assert not S.business_blocked("Spot On Cafe", "Ludhiana")
    assert not S.number_blocked("9876543210")


# ── lessons: thresholds, no fake confidence ─────────────────────────────────

def _results(db, n, *, opener="baseline", connected=True, outcome="NOT_INTERESTED",
             bt="cafe", hour="morning", conf="OBSERVED", objections=None, lead_id=999):
    for i in range(n):
        db.add(CallResult(call_id=f"{opener}-{outcome}-{hour}-{conf}-{i}-{id(object())}",
                          lead_id=lead_id, status="FINAL", source="voice_agent",
                          outcome=outcome if connected else "NO_ANSWER", connected=connected,
                          opener_variant=opener, script_variant=V.SCRIPT_VARIANT,
                          business_type=bt, hour_bucket=hour, language="hi-Latn",
                          objections=objections or [], confidence=conf,
                          started_at=datetime.utcnow() - timedelta(days=1)))
    db.commit()


def test_no_lesson_below_minimum_sample(db, monkeypatch):
    monkeypatch.setenv("CALL_LESSON_MIN_ATTEMPTS", "30")
    monkeypatch.setenv("CALL_LESSON_MIN_CONNECTED", "10")
    _results(db, 5, opener="baseline", outcome="INTERESTED")
    _results(db, 5, opener="question_first", outcome="NOT_INTERESTED")
    agg = L.aggregate(db)
    assert agg["lessons"] == []
    assert agg["overall"]["connect_rate"]["actionable"] is False


def test_lessons_emerge_past_threshold_and_ignore_low_confidence(db, monkeypatch):
    monkeypatch.setenv("CALL_LESSON_MIN_ATTEMPTS", "30")
    monkeypatch.setenv("CALL_LESSON_MIN_CONNECTED", "10")
    _results(db, 30, opener="question_first", outcome="INTERESTED", objections=["PRICE"])
    _results(db, 30, opener="baseline", outcome="NOT_INTERESTED", objections=["PRICE"])
    _results(db, 40, connected=False, hour="evening")
    _results(db, 200, opener="baseline", outcome="INTERESTED", conf="LOW_CONFIDENCE")
    agg = L.aggregate(db)
    assert agg["overall"]["attempts"] == 100
    best = [x for x in agg["lessons"] if x["dimension"] == "opener_variant"]
    assert best and best[0]["value"] == "question_first"
    hours = [x for x in agg["lessons"] if x["dimension"] == "hour_bucket"]
    assert hours and hours[0]["value"] == "morning"
    assert any(x.get("objection") == "PRICE" for x in agg["lessons"])


# ── variants ────────────────────────────────────────────────────────────────

def test_unapproved_variants_are_never_selected(monkeypatch):
    monkeypatch.delenv("CALL_OPENER_VARIANTS_APPROVED", raising=False)
    for i in range(20):
        assert V.select_opener(lead_id=i, attempt=1, rows=[], business_type="cafe")["opener_variant"] == "baseline"


def test_explore_below_threshold_then_exploit(db, monkeypatch):
    monkeypatch.setenv("CALL_OPENER_VARIANTS_APPROVED", "question_first")
    monkeypatch.setenv("CALL_LESSON_MIN_CONNECTED", "10")
    _results(db, 12, opener="baseline", outcome="NOT_INTERESTED")
    rows = L.learnable_rows(db)
    pick = V.select_opener(lead_id=1, attempt=1, rows=rows, business_type="cafe")
    assert pick["mode"] == "explore_below_threshold" and pick["opener_variant"] == "question_first"
    _results(db, 12, opener="question_first", outcome="INTERESTED")
    rows = L.learnable_rows(db)
    pick = V.select_opener(lead_id=1, attempt=1, rows=rows, business_type="cafe", eps=0.0)
    assert pick["mode"] == "exploit" and pick["opener_variant"] == "question_first"
    # never repeat the opener already used with this lead when there is another
    pick = V.select_opener(lead_id=1, attempt=2, rows=rows, business_type="cafe", eps=0.0,
                           used_with_lead=["question_first"])
    assert pick["opener_variant"] == "baseline"


# ── brief ───────────────────────────────────────────────────────────────────

def test_brief_refuses_standing_rule_and_dnc_leads(db):
    assert B.build_call_brief(db, _lead(db, phone="+918591977190"))["skip"]
    assert B.build_call_brief(db, _lead(db, company="Salt'n Peppr", city="Faridkot", phone="9000000001"))["skip"]
    assert B.build_call_brief(db, _lead(db, do_not_call=True, phone="9000000002"))["skip"]
    assert not B.build_call_brief(db, _lead(db, phone="9000000003"))["skip"]


def test_brief_refuses_after_a_stop_outcome(monkeypatch, db):
    lead = _dialled(db, _lead(db), call_ref="s1")
    _client(monkeypatch, db).post("/api/v1/founder/ai-call-status", headers=H,
                                  json={"lead_id": lead.id, "reason": "NO_ANSWER", "call_ref": "s1"})
    assert not B.build_call_brief(db, lead)["skip"]
    lead2 = _dialled(db, _lead(db, phone="9000000009"), call_ref="s2")
    _client(monkeypatch, db).post("/api/v1/founder/ai-call-outcome", headers=H,
                                  json={"lead_id": lead2.id, "outcome": "WRONG_PERSON", "call_ref": "s2"})
    b = B.build_call_brief(db, lead2)
    assert b["skip"] and any("WRONG_PERSON" in x for x in b["skip_reasons"])


def test_brief_adapts_to_prior_call(monkeypatch, db):
    """Concrete adaptation: last time the owner said the price sounded high and
    asked for a callback -> the next brief waits until then, avoids the same
    opener and carries the PRICE handling note."""
    monkeypatch.setenv("CALL_OPENER_VARIANTS_APPROVED", "question_first,purity_claims")
    lead = _dialled(db, _lead(db), call_ref="p1", opener="baseline")
    now = datetime.utcnow()
    _client(monkeypatch, db).post("/api/v1/founder/ai-call-outcome", headers=H, json={
        "lead_id": lead.id, "outcome": "CALLBACK_REQUESTED", "callback_window": "tomorrow 11am",
        "call_ref": "p1", "answered_at": (now - timedelta(seconds=60)).isoformat(),
        "turns": [{"role": "assistant", "text": "Namaste ji"},
                  {"role": "user", "text": "price bahut zyada hoga, abhi busy hoon kal call karo"}]})
    db.expire_all()
    b = B.build_call_brief(db, lead, now=now)
    assert b["skip"] and any("callback requested" in x for x in b["skip_reasons"])
    later = B.build_call_brief(db, lead, now=now + timedelta(days=3))
    assert not later["skip"]
    assert later["avoid_openers"] == ["baseline"]
    assert later["opener_variant"] != "baseline"
    assert any(h["objection"] == "PRICE" for h in later["handle_objections"])
    assert "Never quote a figure" in later["prompt_block"]
    assert len(later["prompt_block"]) <= 900
    f = B.dispatch_fields(later, "ref-next")
    assert f["call_ref"] == "ref-next" and f["opener_variant"] == later["opener_variant"]
    assert f["call_brief"] == later["prompt_block"]


# ── dial path (voice_router.place_call replaced by a recorder) ─────────────

class _Placed:
    placed = True
    provider_call_id = "sc-fake-1"
    error = ""
    raw = {}


def _arm(monkeypatch):
    from app.services import voice_router
    sent = []

    def fake_place_call(lead, *, context=None, **kw):
        sent.append({"lead_id": lead.id, "context": dict(context or {})})
        return _Placed()
    monkeypatch.setattr(voice_router, "place_call", fake_place_call)
    monkeypatch.setattr(voice_router, "active", lambda: "nuraveda")
    return sent


def test_dial_path_sends_the_brief_and_opens_a_call_result(monkeypatch, db):
    from app.services.calling_agent import CallingAgentService
    sent = _arm(monkeypatch)
    lead = _lead(db)
    ok, why = CallingAgentService._place_qualification_call(db, lead, pipeline)
    assert ok, why
    assert len(sent) == 1
    ctx = sent[0]["context"]
    assert ctx["call_ref"].startswith(f"L{lead.id}-") or ctx["call_ref"]
    assert ctx["opener_variant"] == "baseline" and "call_brief" in ctx
    assert "opening" in ctx  # statutory disclosure untouched
    row = db.query(CallResult).filter(CallResult.call_id == ctx["call_ref"]).one()
    assert row.status == "OPEN" and row.provider_call_id == "sc-fake-1"
    assert lead.call_status == "CALLING"


def test_dial_path_refuses_standing_rule_leads_before_any_dispatch(monkeypatch, db):
    from app.services.calling_agent import CallingAgentService
    sent = _arm(monkeypatch)
    for kw in ({"phone": "+91 85919 77190"},
               {"company": "SPOT ON Hotel KK Residency", "city": "Muktsar", "phone": "9000000011"},
               {"company": "Salt'n Peppr", "city": "Faridkot", "phone": "9000000012"}):
        lead = _lead(db, **kw)
        ok, why = CallingAgentService._place_qualification_call(db, lead, pipeline)
        assert not ok and "refused" in why
    assert sent == []


# ── reporting + corrections ─────────────────────────────────────────────────

def test_calls_report_and_founder_follow_up(monkeypatch, db):
    client = _client(monkeypatch, db)
    hot = _dialled(db, _lead(db, company="Hot Lead Cafe"), call_ref="h1")
    client.post("/api/v1/founder/ai-call-outcome", headers=H,
                json={"lead_id": hot.id, "outcome": "INTERESTED", "call_ref": "h1",
                      "summary": "wants catalogue and pricing"})
    cold = _dialled(db, _lead(db, company="Cold", phone="9000000020"), call_ref="c1")
    client.post("/api/v1/founder/ai-call-status", headers=H,
                json={"lead_id": cold.id, "reason": "NO_ANSWER", "call_ref": "c1"})
    r = client.get("/api/v1/outreach-intelligence/calls", headers=H)
    assert r.status_code == 200
    rep = r.json()
    assert rep["outcome_breakdown"]["INTERESTED"] == 1 and rep["outcome_breakdown"]["NO_ANSWER"] == 1
    assert [x["company"] for x in rep["founder_follow_up"]] == ["Hot Lead Cafe"]
    assert rep["founder_follow_up"][0]["phone_masked"].endswith("3210")
    assert rep["leads_still_calling"] == 0
    brief = client.get(f"/api/v1/outreach-intelligence/lead/{cold.id}/call-brief", headers=H).json()
    assert brief["dialled"] is False and brief["prior_call_count"] == 1
    assert client.get(f"/api/v1/outreach-intelligence/lead/{cold.id}/call-brief").status_code == 503


def test_reconcile_endpoint_defaults_to_dry_run(monkeypatch, db):
    lead = _dialled(db, _lead(db), minutes_ago=600)
    client = _client(monkeypatch, db)
    assert client.post("/api/v1/outreach-intelligence/calls/reconcile", json={}).status_code == 503
    r = client.post("/api/v1/outreach-intelligence/calls/reconcile", headers=H, json={})
    assert r.json()["mode"] == "dry_run" and r.json()["stuck_calling_leads"] == 1
    db.expire_all()
    assert lead.call_status == "CALLING"


def test_founder_correction_via_corrections_api(monkeypatch, db):
    client = _client(monkeypatch, db)
    lead = _dialled(db, _lead(db), call_ref="k1")
    client.post("/api/v1/founder/ai-call-outcome", headers=H,
                json={"lead_id": lead.id, "outcome": "OTHER", "call_ref": "k1"})
    row = _final(db, lead)
    r = client.post("/api/v1/outreach-intelligence/corrections", headers=H, json={
        "lead_id": lead.id, "call_result_id": row.id, "field": "call_result.outcome",
        "new_value": "sample_requested", "note": "founder listened to recording"})
    assert r.status_code == 200, r.text
    assert r.json()["call_result_confidence"] == "HUMAN_CORRECTED"
    db.expire_all()
    assert row.outcome == "SAMPLE_REQUESTED" and row.source == "founder"
    bad = client.post("/api/v1/outreach-intelligence/corrections", headers=H, json={
        "lead_id": lead.id, "call_result_id": row.id, "field": "outcome", "new_value": "MAYBE"})
    assert bad.status_code == 400
    # a later automatic report cannot overwrite the founder's correction
    C.finalize_call(db, lead, fsm_key="NOT_INTERESTED", source="voice_agent", meta={"call_ref": "k1"})
    assert row.outcome == "SAMPLE_REQUESTED"


def test_both_dial_implementations_carry_the_hooks(monkeypatch, db):
    """calling_agent's original and call_db_lock_patch's replacement (what
    production actually runs via purity_boot_patches) must both refuse on
    standing rules and send the brief."""
    import inspect

    import app.services.call_db_lock_patch as lock_patch
    from app.services import calling_agent
    for src in (inspect.getsource(calling_agent), inspect.getsource(lock_patch)):
        assert "prepare_dial(db, lead)" in src and "after_dial(" in src and "**brief_ctx" in src
    lock_patch.apply_call_placement_lock_fix()
    sent = _arm(monkeypatch)
    lead = _lead(db, phone="9000000031")
    ok, why = calling_agent.CallingAgentService._place_qualification_call(db, lead, pipeline)
    assert ok, why
    ref = sent[0]["context"]["call_ref"]
    assert db.query(CallResult).filter(CallResult.call_id == ref, CallResult.status == "OPEN").count() == 1
    blocked = _lead(db, phone="8591977190")
    ok, why = calling_agent.CallingAgentService._place_qualification_call(db, blocked, pipeline)
    assert not ok and len(sent) == 1
