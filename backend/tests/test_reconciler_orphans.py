"""15 Oct: orphan OPEN call_results rows are closed by the reconciler."""
from __future__ import annotations
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from app.models.models import B2BLead, Base
from app.services.call_intelligence.models import CallResult, ensure_for_session
from conftest import memory_engine
from app.services.call_intelligence.reconciler import (close_orphan_open_rows, close_orphan_call_history_rows)


def test_orphan_open_rows_closed_but_live_calls_kept():
    eng = memory_engine(); Base.metadata.create_all(eng); db = sessionmaker(bind=eng)()
    ensure_for_session(db)
    now = datetime.utcnow()
    moved = B2BLead(company="A", phone="9876500031", call_status="NO_ANSWER")
    live = B2BLead(company="B", phone="9876500032", call_status="CALLING")
    db.add_all([moved, live]); db.commit()
    db.add_all([CallResult(call_id="o1", lead_id=moved.id, status="OPEN", started_at=now - timedelta(hours=2)),
                CallResult(call_id="o2", lead_id=live.id, status="OPEN", started_at=now - timedelta(minutes=50)),
                CallResult(call_id="o3", lead_id=live.id, status="OPEN", started_at=now - timedelta(hours=30))])
    db.commit()
    assert close_orphan_open_rows(db, dry_run=True, now=now) == 2
    assert close_orphan_open_rows(db, dry_run=False, now=now) == 2
    st = {r.call_id: (r.status, r.termination_reason) for r in db.query(CallResult).all()}
    assert st["o1"] == ("FINAL", "UNKNOWN_NO_RESULT") and st["o3"][0] == "FINAL"
    assert st["o2"] == ("OPEN", None)


def test_stale_call_history_rows_close_only_when_lead_is_not_calling():
    from app.models.models import CallHistory
    eng = memory_engine(); Base.metadata.create_all(eng); db = sessionmaker(bind=eng)()
    now = datetime.utcnow()
    completed = B2BLead(company="Completed", phone="9876500041", call_status="COMPLETED")
    live = B2BLead(company="Live", phone="9876500042", call_status="CALLING")
    db.add_all([completed, live]); db.commit()
    stale = CallHistory(lead_id=completed.id, call_date=now-timedelta(hours=8),
                        status="AI_CALL_ATTEMPTED", call_status="CALLING")
    active = CallHistory(lead_id=live.id, call_date=now-timedelta(hours=8),
                         status="AI_CALL_ATTEMPTED", call_status="CALLING")
    fresh = CallHistory(lead_id=completed.id, call_date=now-timedelta(minutes=30),
                        status="AI_CALL_ATTEMPTED", call_status="CALLING")
    db.add_all([stale, active, fresh]); db.commit()
    assert close_orphan_call_history_rows(db, dry_run=True, now=now, orphan_minutes=240) == 1
    assert stale.call_status == "CALLING"
    assert close_orphan_call_history_rows(db, dry_run=False, now=now, orphan_minutes=240) == 1
    db.refresh(stale); db.refresh(active); db.refresh(fresh)
    assert stale.call_status == "RECONCILED"
    assert active.call_status == "CALLING"
    assert fresh.call_status == "CALLING"
    assert completed.call_status == "COMPLETED"
    db.close(); eng.dispose()
