#!/usr/bin/env python3
"""Install an append-only DNC transition audit on the SQLite lead table.

The baseline is a timestamped snapshot of current DNC flags, not a claim about
when or why those flags were originally set.
"""
from __future__ import annotations
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DDL = (
    """CREATE TABLE IF NOT EXISTS dnc_audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        event_at TEXT NOT NULL,
        old_do_not_call INTEGER,
        new_do_not_call INTEGER,
        old_reason TEXT,
        new_reason TEXT,
        actor TEXT NOT NULL DEFAULT 'DATABASE_TRIGGER',
        source TEXT NOT NULL DEFAULT 'b2b_leads_trigger',
        baseline INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ix_dnc_audit_lead_time ON dnc_audit_events(lead_id,event_at)",
    """CREATE TRIGGER IF NOT EXISTS b2b_leads_dnc_ai AFTER INSERT ON b2b_leads
       WHEN COALESCE(NEW.do_not_call,0) <> 0 BEGIN
       INSERT INTO dnc_audit_events(lead_id,event_type,event_at,old_do_not_call,new_do_not_call,old_reason,new_reason,actor,source,baseline)
       VALUES(NEW.id,'DNC_SET',strftime('%Y-%m-%d %H:%M:%S','now'),NULL,1,NULL,NEW.dnc_reason,'DATABASE_TRIGGER','b2b_leads_after_insert',0);
       END""",
    """CREATE TRIGGER IF NOT EXISTS b2b_leads_dnc_au AFTER UPDATE OF do_not_call,dnc_reason ON b2b_leads
       WHEN COALESCE(OLD.do_not_call,0) <> COALESCE(NEW.do_not_call,0)
         OR COALESCE(OLD.dnc_reason,'') <> COALESCE(NEW.dnc_reason,'') BEGIN
       INSERT INTO dnc_audit_events(lead_id,event_type,event_at,old_do_not_call,new_do_not_call,old_reason,new_reason,actor,source,baseline)
       VALUES(NEW.id,
         CASE WHEN COALESCE(OLD.do_not_call,0)=0 AND COALESCE(NEW.do_not_call,0)=1 THEN 'DNC_SET'
              WHEN COALESCE(OLD.do_not_call,0)=1 AND COALESCE(NEW.do_not_call,0)=0 THEN 'DNC_CLEARED'
              ELSE 'DNC_REASON_CHANGED' END,
         strftime('%Y-%m-%d %H:%M:%S','now'),
         COALESCE(OLD.do_not_call,0),COALESCE(NEW.do_not_call,0),OLD.dnc_reason,NEW.dnc_reason,
         'DATABASE_TRIGGER','b2b_leads_after_update',0);
       END""",
)

def install(conn: sqlite3.Connection) -> int:
    for statement in DDL:
        conn.execute(statement)
    existing = conn.execute("SELECT COUNT(*) FROM dnc_audit_events WHERE baseline=1").fetchone()[0]
    if not existing:
        at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("""INSERT INTO dnc_audit_events
            (lead_id,event_type,event_at,old_do_not_call,new_do_not_call,old_reason,new_reason,actor,source,baseline)
            SELECT id,'LEGACY_BASELINE',?,NULL,1,NULL,dnc_reason,'MIGRATION',
                   'state_snapshot_not_historical',1
            FROM b2b_leads WHERE COALESCE(do_not_call,0)=1""", (at,))
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM dnc_audit_events WHERE baseline=1").fetchone()[0]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(Path(__file__).resolve().parents[1] / 'purity_beans.db'))
    args = parser.parse_args()
    con = sqlite3.connect(args.db)
    print({'baseline_rows': install(con),
           'triggers': [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'b2b_leads_dnc_%'")]})
    con.close()
