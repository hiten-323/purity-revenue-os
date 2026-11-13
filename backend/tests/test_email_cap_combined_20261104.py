"""4 Nov 2026 follow-up 2: server + box share one 15/day email cap."""
import json
from app.services import human_pace as hp

NOW = 1793869200.0  # 2026-11-05 14:30 IST


def _setup(tmp_path, monkeypatch, file_n=0, box=()):
    f = tmp_path / "count.json"; f.write_text(json.dumps({"day": hp._today(NOW), "count": file_n}))
    monkeypatch.setenv("EMAIL_DAILY_STATE_FILE", str(f))
    led = tmp_path / "box.jsonl"
    led.write_text("".join(json.dumps(r) + "\n" for r in box))
    monkeypatch.setenv("EMAIL_BOX_LEDGER_FILE", str(led))
    monkeypatch.delenv("EMAIL_DAILY_DB_FILE", raising=False)
    monkeypatch.delenv("EMAIL_DAILY_CAP", raising=False)


def test_box_sends_count_against_server_cap(tmp_path, monkeypatch):
    day = hp._today(NOW)
    _setup(tmp_path, monkeypatch, file_n=10, box=[{"id": f"b{i}", "day": day} for i in range(5)])
    assert hp.server_daily_count(NOW) == 10 and hp.box_sent_today(NOW) == 5
    assert hp.daily_count(NOW) == 15
    ok, why = hp.daily_cap_ok(NOW)
    assert not ok and "15" in why


def test_under_combined_cap_ok(tmp_path, monkeypatch):
    day = hp._today(NOW)
    _setup(tmp_path, monkeypatch, file_n=7, box=[{"id": f"b{i}", "day": day} for i in range(4)])
    assert hp.daily_count(NOW) == 11 and hp.daily_cap_ok(NOW)[0]


def test_box_ledger_ignores_other_days_and_dup_ids(tmp_path, monkeypatch):
    day = hp._today(NOW)
    _setup(tmp_path, monkeypatch, box=[{"id": "a", "day": day}, {"id": "a", "day": day},
                                       {"id": "b", "day": "2026-11-03"}])
    assert hp.box_sent_today(NOW) == 1


def test_no_live_ledger_under_pytest(monkeypatch):
    monkeypatch.delenv("EMAIL_BOX_LEDGER_FILE", raising=False)
    assert hp._box_ledger() == ""
