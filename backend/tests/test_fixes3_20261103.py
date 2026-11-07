"""3 Nov 2026 (Hiten 11:49 IST) fixes: (1) a call that failed before ringing is
logged as PRE_RING_FAILURE / SIP code, not "rang Ns"; (2) QUARANTINED is blocked
on every dial route; (3) unproven scraped 6-series numbers are held.
In-memory DB only; nothing dials or sends."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ["AI_CALLING_ENABLED"] = "0"
os.environ["AUTO_AI_CALLING_ENABLED"] = "0"

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base, CallHistory
from app.services import founder_call_pipeline as p
from app.services import preference_registry as pref
from app.services.call_intelligence import capture
from app.services.call_intelligence import six_series_hold as H
from app.services.call_intelligence.models import CallResult
from conftest import memory_engine


@pytest.fixture
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    f = tmp_path / "dnd.txt"
    f.write_text("# empty\n", encoding="utf-8")
    monkeypatch.setenv("DND_SUPPRESSION_FILE", str(f))
    pref._cache_key = None
    return f


def _lead(db, company, phone, trust="DISCOVERED", source="Perplexity, BraveSearch, IndiaMart", **kw):
    l = B2BLead(company=company, phone=phone, segment="horeca", phone_trust=trust,
                phone_source=source, **kw)
    db.add(l)
    db.commit()
    return l


# ---------------- fix 1 ----------------
def test_pre_ring_correction_relabels_call_history_row(db):
    lead = _lead(db, "Early Media Cafe", "+91-98140-11111")
    now = datetime.utcnow()
    db.add(CallHistory(lead_id=lead.id, call_date=now - timedelta(seconds=30), status="AI_NO_ANSWER",
                       call_status="ENDED", summary="not answered: rang 19s, caller left before answering"))
    row = CallResult(call_id="ppp-x-1", lead_id=lead.id, status="FINAL", source="voice_agent",
                     outcome="NO_ANSWER", termination_reason="NOT_ANSWERED", evidence={},
                     created_at=now - timedelta(seconds=20))
    db.add(row)
    lead.ai_call_count = 1
    db.commit()
    out = capture.correct_generic_no_answer(db, lead, call_ref="ppp-x-1",
                                            reason="PRE_RING_FAILURE", sip_status_code=400)
    assert out["corrected"] == "DISPATCH_FAILED"
    h = db.query(CallHistory).filter_by(lead_id=lead.id).one()
    assert h.status == "DISPATCH_FAILED" and h.call_status == "DISPATCH_FAILED"
    assert h.summary.startswith("PRE_RING_FAILURE: SIP 400") and "not a no-answer" in h.summary
    assert lead.phone_trust == "DISCOVERED"  # first strike only: normal rules


def test_relabel_never_touches_real_or_old_rows(db):
    lead = _lead(db, "Old Row Cafe", "+91-98140-11112")
    now = datetime.utcnow()
    db.add(CallHistory(lead_id=lead.id, call_date=now - timedelta(days=2), status="AI_NO_ANSWER",
                       call_status="ENDED", summary="not answered: rang 30s, caller left before answering"))
    db.add(CallHistory(lead_id=lead.id, call_date=now, status="AI_CALLEE_HUNGUP",
                       call_status="COMPLETED", summary="hung up"))
    db.commit()
    assert capture.relabel_pre_ring_history(db, lead, code=400, around=now) == 0


def test_pre_ring_summary_without_code():
    assert capture.pre_ring_summary(None).startswith("PRE_RING_FAILURE (PRE_RING_FAILURE")


# ---------------- fix 2 ----------------
def test_quarantined_in_phone_trust_blocked():
    assert "QUARANTINED" in p.PHONE_TRUST_BLOCKED
    for t in ("INVALID", "SHARED_QUARANTINE", "SIP_INVALID", "DEAD_SERIES"):
        assert t in p.PHONE_TRUST_BLOCKED


def test_may_place_ai_call_refuses_quarantined(db, registry):
    lead = _lead(db, "Shared Directory", "+91-98140-11113", trust="QUARANTINED")
    ok, why = p.may_place_ai_call(lead)
    assert not ok and "QUARANTINED" in why


def test_voice_router_chokepoint_refuses_quarantined(db, monkeypatch):
    from app.services import voice_router
    monkeypatch.setattr(voice_router, "kill_switch_engaged", lambda: False)
    monkeypatch.setattr(voice_router, "calling_switched_on", lambda: True)
    monkeypatch.setattr(p, "call_hours_open", lambda lead=None: (True, "open"))
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "ok"))
    monkeypatch.setattr(voice_router, "active", lambda: "nuraveda")
    lead = _lead(db, "Shared Directory 2", "+91-98140-11114", trust="QUARANTINED")
    called = []
    monkeypatch.setattr(voice_router, "_adapter", lambda name: called.append(name))
    res = voice_router.place_call(lead, dry_run=True)
    assert not res.placed and "QUARANTINED" in (res.error or "") and not called


# ---------------- fix 3 ----------------
def test_unproven_scraped_six_series_is_held(db):
    lead = _lead(db, "Six Unproven", "+91-63504-27019")
    assert (H.hold_reason(db, lead) or "").startswith(H.HOLD_PREFIX)


def test_six_series_that_rang_is_not_held(db):
    lead = _lead(db, "Six Rang", "+91-63504-27029")
    db.add(CallResult(call_id="ppp-six-2", lead_id=lead.id, status="FINAL", source="engine",
                      outcome="BUSY", sip_status_code=486, evidence={}))
    db.commit()
    assert H.hold_reason(db, lead) is None


def test_six_series_rang_on_other_lead_same_number_not_held(db):
    a = _lead(db, "Six Twin A", "+91-63504-27039")
    b = _lead(db, "Six Twin B", "+91-63504-27039")
    db.add(CallResult(call_id="ppp-six-3", lead_id=a.id, status="FINAL", source="engine",
                      outcome="NO_ANSWER", connected=True, evidence={}))
    db.commit()
    assert H.hold_reason(db, b) is None


def test_verified_or_whatsapp_or_inbound_six_series_not_held(db):
    assert H.hold_reason(db, _lead(db, "Six PV", "+91-63504-27049", phone_verified=True)) is None
    assert H.hold_reason(db, _lead(db, "Six WA", "+91-63504-27059", whatsapp_verified=True)) is None
    assert H.hold_reason(db, _lead(db, "Six Ver", "+91-63504-27069", trust="VERIFIED")) is None
    assert H.hold_reason(db, _lead(db, "Six Inbound", "+91-63504-27079", source="inbound_whatsapp")) is None


def test_other_series_never_held(db):
    for i, ph in enumerate(("+91-98140-22221", "+91-78140-22222", "+91-88140-22223")):
        assert H.hold_reason(db, _lead(db, f"Other {i}", ph)) is None


def test_hold_can_be_switched_off(db, monkeypatch):
    lead = _lead(db, "Six Off", "+91-63504-27089")
    monkeypatch.setenv(H.ENV, "0")
    assert H.hold_reason(db, lead) is None


def test_selector_skips_held_but_keeps_proven_six_series(db, registry, monkeypatch):
    from tests.cohort_fixture import admit
    from app.services import ai_call_cycle
    held = _lead(db, "Six Held Cold", "+91-63504-27099")
    proven = _lead(db, "Six Proven Cold", "+91-63514-27109", phone_verified=True)
    nine = _lead(db, "Nine Cold", "+91-98140-33331")
    admit(monkeypatch, db, [held, proven, nine], calls=True)
    names = [c.company for c in ai_call_cycle.select_call_candidates(db, limit=50)]
    assert "Nine Cold" in names and "Six Proven Cold" in names
    assert "Six Held Cold" not in names, names


def test_prepare_dial_refuses_held(db):
    from app.services.call_intelligence.dial_hooks import prepare_dial
    lead = _lead(db, "Six Prepare", "+91-63514-27119")
    refusal, *_ = prepare_dial(db, lead)
    assert refusal and refusal.startswith(H.HOLD_PREFIX)
