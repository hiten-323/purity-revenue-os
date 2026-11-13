import importlib.util
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_dnc_audit.py"
spec = importlib.util.spec_from_file_location("install_dnc_audit", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_dnc_audit_baseline_and_transitions_are_append_only():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE b2b_leads(id INTEGER PRIMARY KEY, do_not_call BOOLEAN, dnc_reason TEXT)")
    db.execute("INSERT INTO b2b_leads VALUES(1,1,'LEGACY_SUPPRESSION')")
    assert mod.install(db) == 1
    assert mod.install(db) == 1  # idempotent baseline
    db.execute("UPDATE b2b_leads SET do_not_call=0,dnc_reason='founder override' WHERE id=1")
    db.execute("UPDATE b2b_leads SET do_not_call=1,dnc_reason='new opt-out' WHERE id=1")
    events = db.execute("SELECT event_type,old_do_not_call,new_do_not_call,old_reason,new_reason,baseline FROM dnc_audit_events ORDER BY id").fetchall()
    assert events[0][0] == "LEGACY_BASELINE" and events[0][5] == 1
    assert events[1][0] == "DNC_CLEARED" and events[1][1:3] == (1,0)
    assert events[2][0] == "DNC_SET" and events[2][1:3] == (0,1)
    assert events[2][4] == "new opt-out"
