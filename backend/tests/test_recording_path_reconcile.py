import sqlite3
from pathlib import Path
from scripts.reconcile_recording_paths import run

def test_only_exact_call_reference_is_backfilled(tmp_path, monkeypatch):
    import scripts.reconcile_recording_paths as rec
    monkeypatch.setattr(rec, "valid_audio", lambda path: True)
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE call_results(id INTEGER PRIMARY KEY, call_id TEXT, recording_path TEXT, outcome TEXT)")
    db.executemany("INSERT INTO call_results VALUES(?,?,?,?)", [(1,"ppp-1-abc",None,None),(2,"ppp-2-def",None,None)])
    exact = tmp_path/"2026-11-05"/"ppp-1-abc"/"audio.ogg"
    exact.parent.mkdir(parents=True); exact.write_bytes(b"OggS-test")
    unmatched = tmp_path/"2026-11-05"/"ppp-999-unknown"/"audio.ogg"
    unmatched.parent.mkdir(parents=True); unmatched.write_bytes(b"OggS-test")
    result = run(db,tmp_path)
    assert result["paths_updated"] == 1
    assert db.execute("select recording_path from call_results where id=1").fetchone()[0] == str(exact)
    assert db.execute("select recording_path from call_results where id=2").fetchone()[0] is None
    assert result["unlinked_files"] == 1
