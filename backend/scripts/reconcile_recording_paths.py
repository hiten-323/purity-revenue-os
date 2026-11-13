#!/usr/bin/env python3
"""Restore only exact call_id -> valid existing audio.ogg recording references."""
import os
import sqlite3
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DB = Path(os.getenv("PURITY_DB_PATH", BACKEND / "purity_beans.db"))
RECORDINGS = Path(os.getenv("CALL_RECORDINGS_DIR", "/opt/purity-revenue-os/recordings"))

def valid_audio(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=8)
        return p.returncode == 0 and float(p.stdout.strip()) > 0
    except Exception:
        return False

def run(db, recordings):
    rows = db.execute("SELECT id,call_id,recording_path,outcome FROM call_results WHERE call_id IS NOT NULL").fetchall()
    by_call = {str(row[1]): row for row in rows}
    updated = unlinked = invalid = pre_ring = 0
    files = list(recordings.glob("*/*/audio.ogg"))
    for audio in files:
        row = by_call.get(audio.parent.name)
        if not row:
            unlinked += 1
            continue
        if str(row[3] or "").upper() == "DISPATCH_FAILED":
            pre_ring += 1
            continue
        current = str(row[2] or "").strip()
        if current and Path(current).is_file():
            continue
        if not valid_audio(audio):
            invalid += 1
            continue
        db.execute("UPDATE call_results SET recording_path=? WHERE id=?", (str(audio), row[0]))
        updated += 1
    db.commit()
    return {"files_scanned": len(files), "paths_updated": updated,
            "unlinked_files": unlinked, "invalid_files": invalid, "pre_ring_skipped": pre_ring}

if __name__ == "__main__":
    con = sqlite3.connect(DB)
    try:
        print("RECORDING_PATH_RECONCILIATION", run(con, RECORDINGS))
        print("DB_CHECK", con.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        con.close()
