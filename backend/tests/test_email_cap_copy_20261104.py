"""4 Nov 2026 (Hiten "stop,fix all,resume"): hard 15/day email cap and copy rules."""
import sqlite3
from datetime import datetime, timedelta


def _db(tmp_path, n_today):
    p = tmp_path / "d.db"
    con = sqlite3.connect(p)
    con.execute("create table email_drafts(id integer primary key, status text, sent_at text)")
    now_utc = datetime.utcnow()
    for i in range(n_today):
        con.execute("insert into email_drafts(status, sent_at) values('SENT', ?)", (now_utc.isoformat(sep=' '),))
    con.execute("insert into email_drafts(status, sent_at) values('SENT', ?)", ((now_utc - timedelta(days=3)).isoformat(sep=' '),))
    con.execute("insert into email_drafts(status, sent_at) values('FAILED', ?)", (now_utc.isoformat(sep=' '),))
    con.commit(); con.close()
    return str(p)


def test_cap_cannot_be_disabled_or_raised(monkeypatch):
    from app.services import human_pace as h
    for raw, want in [("0", 15), ("-1", 15), ("40", 15), ("16", 15), ("15", 15), ("10", 10)]:
        monkeypatch.setenv("EMAIL_DAILY_CAP", raw)
        assert h.effective_daily_cap() == want, raw


def test_db_sends_count_even_when_counter_is_lost(monkeypatch, tmp_path):
    from app.services import human_pace as h
    monkeypatch.setenv("EMAIL_DAILY_CAP", "15")
    monkeypatch.setenv("EMAIL_DAILY_STATE_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("EMAIL_DAILY_DB_FILE", _db(tmp_path, 15))
    assert h.db_sent_today() == 15
    ok, why = h.daily_cap_ok()
    assert not ok and "15" in why


def test_under_cap_ok(monkeypatch, tmp_path):
    from app.services import human_pace as h
    monkeypatch.setenv("EMAIL_DAILY_CAP", "40")
    monkeypatch.setenv("EMAIL_DAILY_STATE_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("EMAIL_DAILY_DB_FILE", _db(tmp_path, 14))
    assert h.daily_cap_ok()[0]


def test_wording_gate_blocks_gst_and_100pct():
    from app.services.wording_gate import check
    assert not check("", "We are FSSAI licensed, GST compliant.")[0]
    assert not check("", "Full GSTIN invoice")[0]
    assert not check("", "100% coffee")[0]
    assert check("", "Pure coffee, FSSAI licensed and MSME registered. +91 90849 58495")[0]


def test_templates_have_no_gst_or_100pct():
    import re
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "app"
    from app.services import outreach_engine as oe, outreach_templates as ot
    for div in ("distributor", "retail", "wholesale"):
        line = oe.margin_line(div)
        if line:
            assert "30% margin (25% trade discount + 5% cash discount)" in line
    for k, v in ot._SEGMENT_VALUEPROP.items():
        assert not re.search(r"\bGST\b|100\s*%", v), k
    src = (base / "services" / "email_sender.py").read_text()
    assert "GST Compliant" not in src and "GST-compliant" not in src
