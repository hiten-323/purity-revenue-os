"""4 Nov 2026 audit items f + h."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import founder_handoff_alert as fha
from app.services.founder_call_pipeline import set_followup_after_call

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=IST)


def _lead(**kw):
    d = dict(id=7, business_name="Test Cafe", city="Pune", phone="+919000001234", next_followup_date=None)
    d.update(kw)
    return SimpleNamespace(**d)


def test_followup_set_tomorrow_for_interest():
    l = _lead()
    assert set_followup_after_call(l, "INTERESTED", now=NOW) == "2026-11-05"
    assert l.next_followup_date == "2026-11-05"


def test_followup_ignored_for_negative():
    l = _lead()
    assert set_followup_after_call(l, "NOT_INTERESTED", now=NOW) is None
    assert l.next_followup_date is None


def test_followup_keeps_earlier_upcoming():
    l = _lead(next_followup_date="2026-11-04")
    assert set_followup_after_call(l, "CALLBACK_REQUESTED", now=NOW) is None
    assert l.next_followup_date == "2026-11-04"


def test_followup_replaces_stale_past_date():
    l = _lead(next_followup_date="2026-11-02")
    assert set_followup_after_call(l, "CATALOGUE_REQUESTED", now=NOW) == "2026-11-05"


def test_alert_queued_not_sent_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(fha, "QUEUE", tmp_path / "q.jsonl")
    monkeypatch.delenv("FOUNDER_ALERT_SEND", raising=False)
    called = []
    monkeypatch.setattr(fha, "_emit_klaviyo", lambda *a: called.append(1) or (True, "x"))
    rec = fha.on_call_outcome(_lead(), None, "MEETING_REQUESTED", summary="wants a visit", now=NOW)
    assert rec["status"] == "QUEUED" and rec["sent"] is False and not called
    assert "1234" in rec["text"] and "9000001234" not in rec["text"]
    # deduped same day
    assert fha.on_call_outcome(_lead(), None, "MEETING_REQUESTED", now=NOW) is None
    assert len((tmp_path / "q.jsonl").read_text().splitlines()) == 1


def test_alert_ignores_negative(tmp_path, monkeypatch):
    monkeypatch.setattr(fha, "QUEUE", tmp_path / "q.jsonl")
    assert fha.on_call_outcome(_lead(), None, "NOT_INTERESTED", now=NOW) is None
